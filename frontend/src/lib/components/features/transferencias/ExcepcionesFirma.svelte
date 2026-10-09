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
  ExcepcionesFirma.svelte — Constancias de «firma inobtenible» del acta de
  transferencia (AREA-07-10, ADR-030): lista, declarar y revocar.

  Espejo del backend, nunca su sustituto:
   - Declarar/revocar solo se ofrece a `USUA_PERM_ADMIN` ≥ 3 con `PERM_FIRMA` ≥ 2 que NO
     sea parte de la transferencia (`creada_por`/`enviada_por`/`decidida_por`).
   - Solo se puede declarar a un rol sin firma ni excepción VIGENTE (una revocada admite
     declarar de nuevo: cadena con `ordinal`, FIRMA-2), con el acta ya recibida.
   - El soporte se ELIGE (radicado y anexo, ambos obligatorios): no es texto libre.
   - El código de verificación (app autenticadora) se ofrece siempre: quien declara o revoca con certificado de firma activo
     DEBE firmar (FIRMA-2), y la pantalla no conoce su certificado; si falta, el servidor
     responde 422 y el campo pasa a obligatorio.
   - Verificar el fundamento (HMAC contra tres copias) solo se ofrece a administradores ≥ 3.
   - Declarar es irreversible salvo revocación: lleva una confirmación explícita.
  El `fundamento` completo llega solo a administradores ≥ 3; al resto, motivo, referencia y
  huella.
-->
<script>
  import { createEventDispatcher, tick } from "svelte";
  import { permissionLevels, userId } from "$lib/stores/auth.js";
  import { notifySuccess } from "$lib/stores/notifications.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import {
    ROL_LABELS,
    MOTIVOS,
    ETIQUETA_MOTIVO,
    FUNDAMENTO_MIN,
    FUNDAMENTO_MAX,
    puedeGestionarExcepciones,
    excepcionesDe,
    rolesDeclarables,
    rolesRevocables,
    longitudFundamentoValida,
    describirPersona,
    huellaFundamento,
    huellaRevocacion,
    algoritmoHuella,
    CAUSAS_REVOCACION,
    ETIQUETA_CAUSA,
    puedeReintentarWorm,
    resumenReintento,
    descargarXml,
    AVISO_FUNDAMENTO,
    AYUDA_HUELLA,
    etiquetaAdvertencia,
    etiquetaMotivosEnlace,
    puedeVerificarFundamento,
    resumenVerificacionFundamento,
    SOPORTE_NO_VISIBLE,
  } from "$lib/utils/firmaInobtenible.js";

  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";

  /** @type {any} */
  export let transferencia = null;
  /** @type {any} */
  export let acta = null;
  /** @type {any} */
  export let verificacion = null;
  /** Radicados del expediente entre los que se elige el soporte: `{radicado_id, tracking_number}`. */
  /** @type {Array<{radicado_id: string, tracking_number: string}>} */
  export let radicados = [];

  const dispatch = createEventDispatcher();

  /** Tras un acto que recarga el acta, el foco va al título de la sección (a11y). */
  async function avisarCambio() {
    dispatch("changed");
    await tick();
    setTimeout(() => document.getElementById("exc-titulo")?.focus(), 0);
  }

  $: excepciones = excepcionesDe(verificacion, acta);
  $: puede = puedeGestionarExcepciones({
    levels: $permissionLevels,
    userId: $userId,
    transferencia,
  });
  $: declarables = rolesDeclarables({ transferencia, verificacion, acta });
  $: revocables = rolesRevocables(excepciones);
  $: puedeVerificar = puedeVerificarFundamento($permissionLevels);

  /** Rol y n.º de declaración para distinguir botones repetidos. @param {any} e */
  const quien = (e) => `${ROL_LABELS[e.rol] ?? e.rol}, declaración ${e.ordinal ?? 1}`;

  /** @param {string|null|undefined} iso */
  const fmt = (iso) => (iso ? formatearFechaHora(iso) : "—");

  // ---------------------------------------------------------------- declarar
  let declararAbierto = false;
  let dRol = "";
  let dMotivo = "";
  let dFundamento = "";
  let dRadicado = "";
  let dAnexo = "";
  let dTotp = "";
  let dConfirma = false;
  let dTotpForzado = false;
  let dEnviando = false;
  let dError = "";
  /** @type {Array<{value: string, label: string}>} */
  let anexosOpciones = [];
  let anexosEstado = "idle"; // idle | cargando | listo | error
  let anexosSolicitud = 0;

  function abrirDeclarar() {
    dRol = declarables.length === 1 ? declarables[0] : "";
    dMotivo = "";
    dFundamento = "";
    dRadicado = "";
    dAnexo = "";
    dTotp = "";
    dConfirma = false;
    dTotpForzado = false;
    dError = "";
    anexosOpciones = [];
    anexosEstado = "idle";
    declararAbierto = true;
  }

  async function cargarAnexos(radicadoId) {
    dAnexo = "";
    anexosOpciones = [];
    if (!radicadoId || !transferencia?.expediente_id) {
      anexosEstado = "idle";
      return;
    }
    const solicitud = ++anexosSolicitud;
    anexosEstado = "cargando";
    try {
      const res = await fetch(
        `/expedientes/${encodeURIComponent(transferencia.expediente_id)}/api/anexos/${encodeURIComponent(radicadoId)}`,
      );
      if (solicitud !== anexosSolicitud) return;
      if (!res.ok) {
        anexosEstado = "error";
        return;
      }
      const lista = await res.json().catch(() => []);
      if (solicitud !== anexosSolicitud) return;
      anexosOpciones = (Array.isArray(lista) ? lista : [])
        .filter((a) => a?.file_id)
        .map((a) => ({ value: a.file_id, label: a.filename ?? a.file_id }));
      anexosEstado = "listo";
    } catch {
      if (solicitud === anexosSolicitud) anexosEstado = "error";
    }
  }
  // Depende SOLO del radicado elegido (la función no es dependencia reactiva).
  $: if (declararAbierto) cargarAnexos(dRadicado);

  $: totpObligatorio = dTotpForzado;
  $: radicadoOpciones = [
    { value: "", label: radicados.length ? "Elija el radicado de soporte" : "Sin radicados" },
    ...radicados.map((r) => ({ value: r.radicado_id, label: r.tracking_number })),
  ];
  $: declararListo =
    !!dRol &&
    !!dMotivo &&
    !!dRadicado &&
    !!dAnexo &&
    longitudFundamentoValida(dFundamento) &&
    (!totpObligatorio || dTotp.trim() !== "") &&
    dConfirma;

  async function confirmarDeclarar() {
    if (!declararListo || dEnviando) return;
    dEnviando = true;
    dError = "";
    try {
      const res = await fetch(
        `/transferencias/api/${encodeURIComponent(transferencia.id)}/acta/firma-inobtenible`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            rol: dRol,
            motivo: dMotivo,
            fundamento: dFundamento.trim(),
            referencia_radicado_id: dRadicado,
            referencia_anexo_id: dAnexo,
            ...(dTotp.trim() ? { totp_code: dTotp.trim() } : {}),
          }),
        },
      );
      const result = await res.json().catch(() => ({}));
      if (!res.ok) {
        const codigo = codigoError(result);
        if (res.status === 422 && codigo === "firma_personal_declarante_requerida") {
          dTotpForzado = true;
        }
        if (res.status === 401 && codigo === "totp_incorrecto") dTotp = "";
        if (codigo === "declarante_sin_certificado") dTotpForzado = false;
        dError = mensajeError(
          result,
          `No se pudo declarar la firma inobtenible (error ${res.status}).`,
        );
        return;
      }
      declararAbierto = false;
      notifySuccess(
        `Firma inobtenible declarada para ${ROL_LABELS[dRol] ?? dRol}. Queda constancia sellada y se puede revocar.`,
      );
      avisarCambio();
    } catch {
      dError = "No se pudo conectar con el servidor. No se declaró nada.";
    } finally {
      dEnviando = false;
    }
  }

  // ---------------------------------------------------- verificar fundamento
  /** @type {Record<string, {cargando: boolean, error: string, resultado: any}>} */
  let verif = {};

  /** @param {string} id */
  async function verificarFundamento(id) {
    verif = { ...verif, [id]: { cargando: true, error: "", resultado: null } };
    try {
      const res = await fetch(
        `/transferencias/api/${encodeURIComponent(transferencia.id)}/acta/firma-inobtenible/${encodeURIComponent(id)}/fundamento/verificacion`,
      );
      const result = await res.json().catch(() => ({}));
      verif = {
        ...verif,
        [id]: res.ok
          ? { cargando: false, error: "", resultado: result }
          : {
              cargando: false,
              error: mensajeError(
                result,
                `No se pudo verificar el fundamento (error ${res.status}).`,
              ),
              resultado: null,
            },
      };
    } catch {
      verif = {
        ...verif,
        [id]: { cargando: false, error: "No se pudo conectar con el servidor.", resultado: null },
      };
    }
  }

  // ------------------------------------------------- fundamento bajo demanda
  /** @type {Record<string, {cargando: boolean, error: string, texto: string|null}>} */
  let fund = {};

  /** Carga el texto UNA vez (cada lectura queda asentada); ocultar lo descarta. @param {string} id @param {string} acto */
  async function alternarFundamento(id, acto) {
    const k = `${acto}:${id}`;
    if (fund[k]?.cargando) return;
    if (fund[k]?.texto != null) {
      fund = { ...fund, [k]: { cargando: false, error: "", texto: null } };
      return;
    }
    fund = { ...fund, [k]: { cargando: true, error: "", texto: null } };
    try {
      const res = await fetch(
        `/transferencias/api/${encodeURIComponent(transferencia.id)}/acta/firma-inobtenible/${encodeURIComponent(id)}/fundamento?acto=${acto}`,
      );
      const result = await res.json().catch(() => ({}));
      fund = {
        ...fund,
        [k]:
          res.ok && typeof result.fundamento === "string"
            ? { cargando: false, error: "", texto: result.fundamento }
            : {
                cargando: false,
                error: mensajeError(result, `No se pudo leer el fundamento (error ${res.status}).`),
                texto: null,
              },
      };
    } catch {
      fund = {
        ...fund,
        [k]: { cargando: false, error: "No se pudo conectar con el servidor.", texto: null },
      };
    }
  }

  // -------------------------------------------------------- descargar sobre
  /** @type {Record<string, {cargando: boolean, error: string}>} */
  let sobres = {};

  /** @param {string} id @param {string} acto */
  async function descargarSobre(id, acto) {
    const k = `${acto}:${id}`;
    if (sobres[k]?.cargando) return;
    sobres = { ...sobres, [k]: { cargando: true, error: "" } };
    try {
      const res = await fetch(
        `/transferencias/api/${encodeURIComponent(transferencia.id)}/acta/firma-inobtenible/${encodeURIComponent(id)}/sobre?acto=${acto}`,
      );
      const result = await res.json().catch(() => ({}));
      if (!res.ok || typeof result.xml_firmado !== "string") {
        sobres = {
          ...sobres,
          [k]: {
            cargando: false,
            error: mensajeError(result, `No se pudo obtener el sobre (error ${res.status}).`),
          },
        };
        return;
      }
      descargarXml(`sobre-${acto}-${id}.xml`, result.xml_firmado);
      sobres = { ...sobres, [k]: { cargando: false, error: "" } };
    } catch {
      sobres = {
        ...sobres,
        [k]: { cargando: false, error: "No se pudo conectar con el servidor." },
      };
    }
  }

  // ------------------------------------------------- reintentar protección WORM
  /** @type {Record<string, {cargando: boolean, error: string, resultado: any}>} */
  let reint = {};

  /** @param {string} id @param {string} acto */
  async function reintentarWorm(id, acto) {
    const k = `${acto}:${id}`;
    reint = { ...reint, [k]: { cargando: true, error: "", resultado: null } };
    const ruta = acto === "revocacion" ? "revocacion/worm/reintentar" : "worm/reintentar";
    try {
      const res = await fetch(
        `/transferencias/api/${encodeURIComponent(transferencia.id)}/acta/firma-inobtenible/${encodeURIComponent(id)}/${ruta}`,
        { method: "POST" },
      );
      const result = await res.json().catch(() => ({}));
      if (res.ok) avisarCambio();
      reint = {
        ...reint,
        [k]: res.ok
          ? { cargando: false, error: "", resultado: result }
          : {
              cargando: false,
              error: mensajeError(
                result,
                `No se pudo reintentar la protección (error ${res.status}).`,
              ),
              resultado: null,
            },
      };
    } catch {
      reint = {
        ...reint,
        [k]: { cargando: false, error: "No se pudo conectar con el servidor.", resultado: null },
      };
    }
  }

  // ----------------------------------------------------------------- revocar
  let revocarAbierto = false;
  let rRol = "";
  let rFundamento = "";
  let rCausa = "";
  let rConfirma = false;
  let rTotp = "";
  let rTotpForzado = false;
  let rEnviando = false;
  let rError = "";

  /** @param {string} rol */
  function abrirRevocar(rol) {
    rRol = rol;
    rFundamento = "";
    rCausa = "";
    rConfirma = false;
    rTotp = "";
    rTotpForzado = false;
    rError = "";
    revocarAbierto = true;
  }

  $: revocarListo =
    !!rRol &&
    !!rCausa &&
    longitudFundamentoValida(rFundamento) &&
    rConfirma &&
    (!rTotpForzado || rTotp.trim() !== "");

  async function confirmarRevocar() {
    if (!revocarListo || rEnviando) return;
    rEnviando = true;
    rError = "";
    try {
      const res = await fetch(
        `/transferencias/api/${encodeURIComponent(transferencia.id)}/acta/firma-inobtenible/revocar`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            rol: rRol,
            causa: rCausa,
            fundamento: rFundamento.trim(),
            ...(rTotp.trim() ? { totp_code: rTotp.trim() } : {}),
          }),
        },
      );
      const result = await res.json().catch(() => ({}));
      if (!res.ok) {
        const codigo = codigoError(result);
        if (res.status === 422 && codigo === "firma_personal_revocante_requerida") {
          rTotpForzado = true;
        }
        if (res.status === 401 && codigo === "totp_incorrecto") rTotp = "";
        rError = mensajeError(result, `No se pudo revocar la excepción (error ${res.status}).`);
        return;
      }
      revocarAbierto = false;
      notifySuccess(`Excepción de ${ROL_LABELS[rRol] ?? rRol} revocada.`);
      avisarCambio();
    } catch {
      rError = "No se pudo conectar con el servidor. No se revocó nada.";
    } finally {
      rEnviando = false;
    }
  }
</script>

<section class="exc" aria-labelledby="exc-titulo" data-testid="excepciones-firma">
  <div class="exc__head">
    <h3 id="exc-titulo" class="exc__title" tabindex="-1">Firmas inobtenibles</h3>
    {#if puede && declarables.length > 0}
      <Button variant="secondary" size="sm" icon="fas fa-user-slash" on:click={abrirDeclarar}>
        Declarar firma inobtenible
      </Button>
    {/if}
  </div>

  {#if excepciones.length === 0}
    <p class="exc__empty">Ninguna parte tiene una excepción de firma declarada.</p>
  {:else}
    <ul class="exc__list">
      {#each excepciones as e (e.id ?? e.rol)}
        <li class="exc__item" class:exc__item--revocada={e.revocada} data-rol={e.rol}>
          <div class="exc__item-head">
            <strong>{ROL_LABELS[e.rol] ?? e.rol}</strong>
            {#if (e.ordinal ?? 1) > 1}
              <span class="exc__ord" data-testid="exc-ordinal">
                Declaración n.º {e.ordinal}{e.sustituye_a ? " (sustituye a una revocada)" : ""}
              </span>
            {/if}
            {#if e.revocada}
              <StatusChip status="neutral" label="Revocada: ya no cubre a esta parte" dot />
            {:else}
              <StatusChip status="warning" label="Vigente" dot />
            {/if}
            {#if !e.firmada}
              <StatusChip status="error" label="Constancia sin firma: no se da por válida" dot />
            {:else if e.sello_valida === false}
              <StatusChip status="error" label="El sello de la constancia no es válido" dot />
            {:else if e.sello_valida === true}
              <StatusChip status="success" label="Constancia sellada y verificada" dot />
            {:else}
              <StatusChip status="info" label="Constancia sellada, sin verificar" dot />
            {/if}
          </div>
          <dl class="exc__dl">
            <div>
              <dt>Motivo</dt>
              <dd>{ETIQUETA_MOTIVO[e.motivo] ?? e.motivo}</dd>
            </div>
            <div>
              <dt>Soporte</dt>
              <dd>
                {#if e.referencia_radicado_numero || e.referencia_anexo_sha256}
                  {#if e.referencia_radicado_numero}
                    Radicado <code>{e.referencia_radicado_numero}</code>
                  {/if}
                  {#if e.referencia_anexo_sha256}
                    · anexo SHA-256 <code class="exc__hash">{e.referencia_anexo_sha256}</code>
                  {/if}
                {:else if !e.referencia_documental && !e.referencia_radicado_id}
                  <em data-testid="soporte-no-visible">{SOPORTE_NO_VISIBLE}</em>
                {:else}
                  <code>{e.referencia_documental}</code>
                {/if}
              </dd>
            </div>
            <div>
              <dt>Declarante</dt>
              <dd>
                {describirPersona(e.declarante_nombre, e.declarante_cargo, e.declarante_fuente)}
              </dd>
            </div>
            <div>
              <dt>Parte sin firma</dt>
              <dd>{describirPersona(e.parte_nombre, e.parte_cargo, e.parte_fuente)}</dd>
            </div>
            <div>
              <dt>Declarada</dt>
              <dd>{fmt(e.declarada_en)}</dd>
            </div>
            <div class="exc__wide">
              <dt>Fundamento</dt>
              <dd>
                {#if fund[`constancia:${e.id}`]?.texto != null}
                  {fund[`constancia:${e.id}`].texto}
                {:else if puedeVerificar}
                  <em>Oculto: use «Mostrar fundamento» (queda registrada su lectura).</em>
                {:else}
                  <em>Reservado: solo lo ve un administrador de nivel 3.</em>
                {/if}
              </dd>
            </div>
            <div class="exc__wide">
              <dt>Huella del fundamento</dt>
              <dd>
                {#if huellaFundamento(e)}
                  <code class="exc__hash">{huellaFundamento(e)}</code>
                  {#if algoritmoHuella(e)}<span class="exc__ord"> ({algoritmoHuella(e)})</span>{/if}
                  <br /><span class="exc__ord">{AYUDA_HUELLA}</span>
                {:else}
                  <em>Constancia anterior a la versión 3: sin huella verificable.</em>
                {/if}
              </dd>
            </div>
            {#if e.revocada}
              <div>
                <dt>Revocada</dt>
                <dd>{fmt(e.revocada_en)}</dd>
              </div>
              <div class="exc__wide">
                <dt>Fundamento de la revocación</dt>
                <dd>
                  {#if fund[`revocacion:${e.id}`]?.texto != null}
                    {fund[`revocacion:${e.id}`].texto}
                  {:else if puedeVerificar}
                    <em>Oculto: use «Mostrar fundamento de la revocación».</em>
                  {:else}
                    <em>Reservado.</em>
                  {/if}
                  {#if huellaRevocacion(e)}
                    <code class="exc__hash">{huellaRevocacion(e)}</code>
                  {/if}
                </dd>
              </div>
              <div>
                <dt>Causa de la revocación</dt>
                <dd>
                  {ETIQUETA_CAUSA[e.revocacion_causa] ?? e.revocacion_causa ?? "No registrada"}
                </dd>
              </div>
              <div>
                <dt>Revocó</dt>
                <dd>
                  {describirPersona(e.revocante_nombre, e.revocante_cargo, e.revocante_fuente)}
                </dd>
              </div>
              <div>
                <dt>Protección de la revocación</dt>
                <dd data-testid="revocacion-worm">
                  {#if e.revocacion_worm_protegido === true}
                    Protegida en el almacén
                  {:else if e.revocacion_worm_protegido === false}
                    Protección pendiente: aún no se puede declarar de nuevo
                  {:else}
                    Sin dato
                  {/if}
                </dd>
              </div>
            {/if}
          </dl>
          {#if e.enlace_valido === false}
            <p class="exc__error" role="status" data-testid="enlace-roto">
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
              Enlace de la cadena roto{e.enlace_motivo
                ? `: ${etiquetaMotivosEnlace(e.enlace_motivo)}`
                : ""}.
            </p>
          {:else if e.enlace_valido === true}
            <p class="exc__ord" data-testid="enlace-ok">Enlace de la cadena verificado.</p>
          {/if}
          {#if e.worm_retain_until}
            <p class="exc__ord" data-testid="worm-retain">
              Sello retenido hasta {fmt(e.worm_retain_until)}.
            </p>
          {/if}
          {#if (e.advertencias ?? []).length > 0}
            <p class="exc__warn">
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
              {e.advertencias.map(etiquetaAdvertencia).join(" · ")}
            </p>
          {/if}
          {#if puedeVerificar && e.id}
            {#each e.revocada ? ["constancia", "revocacion"] : ["constancia"] as acto (acto)}
              {@const f = fund[`${acto}:${e.id}`]}
              <div class="exc__verif">
                <Button
                  variant="ghost"
                  size="sm"
                  icon={f?.texto != null ? "fas fa-eye-slash" : "fas fa-eye"}
                  loading={f?.cargando}
                  on:click={() => alternarFundamento(e.id, acto)}
                >
                  {f?.texto != null ? "Ocultar" : "Mostrar"} fundamento{acto === "revocacion"
                    ? " de la revocación"
                    : ""}<span class="sr-only"> — {quien(e)}</span>
                </Button>
                {#if f?.error}<p class="exc__error">{f.error}</p>{/if}
              </div>
            {/each}
          {/if}
          {#if puedeVerificar && e.id && e.sobre}
            <div class="exc__verif">
              <Button
                variant="ghost"
                size="sm"
                icon="fas fa-file-arrow-down"
                loading={sobres[`constancia:${e.id}`]?.cargando}
                on:click={() => descargarSobre(e.id, "constancia")}
              >
                Descargar sobre<span class="sr-only"> — {quien(e)}</span>
              </Button>
              {#if sobres[`constancia:${e.id}`]?.error}
                <p class="exc__error" role="alert">{sobres[`constancia:${e.id}`].error}</p>
              {/if}
            </div>
          {/if}
          {#if puedeVerificar && e.id && e.revocada && e.revocacion_sobre}
            <div class="exc__verif">
              <Button
                variant="ghost"
                size="sm"
                icon="fas fa-file-arrow-down"
                loading={sobres[`revocacion:${e.id}`]?.cargando}
                on:click={() => descargarSobre(e.id, "revocacion")}
              >
                Descargar sobre de la revocación<span class="sr-only"> — {quien(e)}</span>
              </Button>
              {#if sobres[`revocacion:${e.id}`]?.error}
                <p class="exc__error" role="alert">{sobres[`revocacion:${e.id}`].error}</p>
              {/if}
            </div>
          {/if}
          {#each [["excepcion", "Reintentar protección de la constancia"], ["revocacion", "Reintentar protección de la revocación"]] as [acto, rotulo] (acto)}
            {#if puedeReintentarWorm($permissionLevels, e, acto)}
              {@const r = reint[`${acto}:${e.id}`]}
              <div class="exc__verif">
                <Button
                  variant="ghost"
                  size="sm"
                  icon="fas fa-lock"
                  loading={r?.cargando}
                  on:click={() => reintentarWorm(e.id, acto)}
                >
                  {rotulo}<span class="sr-only"> — {quien(e)}</span>
                </Button>
                <div aria-live="polite">
                  {#if r?.error}
                    <p class="exc__error">{r.error}</p>
                  {:else if r?.resultado}
                    <p data-testid="reintento-resultado">{resumenReintento(r.resultado).texto}</p>
                  {/if}
                </div>
              </div>
            {/if}
          {/each}
          {#if puedeVerificar && e.id}
            <div class="exc__verif">
              <Button
                variant="ghost"
                size="sm"
                icon="fas fa-shield-halved"
                loading={verif[e.id]?.cargando}
                on:click={() => verificarFundamento(e.id)}
              >
                Verificar fundamento<span class="sr-only"> — {quien(e)}</span>
              </Button>
              <div aria-live="polite">
                {#if verif[e.id]?.error}
                  <p class="exc__error">{verif[e.id].error}</p>
                {:else if verif[e.id]?.resultado}
                  {@const r = resumenVerificacionFundamento(verif[e.id].resultado)}
                  <p data-testid="verif-resultado">
                    <StatusChip status={r.status} label={r.texto} dot />
                  </p>
                  {#if r.copias.length}
                    <ul class="exc__copias">
                      {#each r.copias as c (c.nombre)}
                        <li>{c.nombre}: {c.estado}</li>
                      {/each}
                    </ul>
                  {/if}
                {/if}
              </div>
            </div>
          {/if}
          {#if puede && !e.revocada && revocables.includes(e.rol)}
            <Button
              variant="ghost"
              size="sm"
              icon="fas fa-rotate-left"
              on:click={() => abrirRevocar(e.rol)}
            >
              Revocar excepción<span class="sr-only"> — {quien(e)}</span>
            </Button>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<Modal
  open={declararAbierto}
  title="Declarar firma inobtenible"
  size="md"
  on:close={() => (declararAbierto = false)}
>
  <p>
    Declara que una parte del acta <strong>no puede firmar</strong>. Se genera una constancia
    sellada a su nombre que queda conservada de forma permanente. Si usted tiene certificado de
    firma activo, debe firmarla también con su código de verificación.
  </p>
  <div class="exc__form">
    <FormField
      id="exc-rol"
      label="Parte que no puede firmar"
      type="select"
      required
      options={[
        { value: "", label: "Elija la parte" },
        ...declarables.map((r) => ({ value: r, label: ROL_LABELS[r] })),
      ]}
      bind:value={dRol}
    />
    <FormField
      id="exc-motivo"
      label="Motivo"
      type="select"
      required
      options={[{ value: "", label: "Elija el motivo" }, ...MOTIVOS]}
      bind:value={dMotivo}
    />
    <FormField
      id="exc-fundamento"
      label="Fundamento"
      type="textarea"
      rows={4}
      required
      bind:value={dFundamento}
      hint={`${AVISO_FUNDAMENTO} Entre ${FUNDAMENTO_MIN} y ${FUNDAMENTO_MAX} caracteres (${dFundamento.trim().length}). Solo lo verán administradores de nivel 3.`}
    />
    <FormField
      id="exc-radicado"
      label="Radicado de soporte"
      type="select"
      required
      options={radicadoOpciones}
      bind:value={dRadicado}
      hint="Radicado del expediente que respalda la declaración (acta de defunción, resolución de retiro…)."
    />
    {#if dRadicado}
      <FormField
        id="exc-anexo"
        label="Anexo de soporte"
        type="select"
        required
        options={[
          {
            value: "",
            label:
              anexosEstado === "cargando"
                ? "Cargando anexos…"
                : anexosOpciones.length
                  ? "Elija el anexo de soporte"
                  : "El radicado no tiene anexos: elija otro radicado",
          },
          ...anexosOpciones,
        ]}
        bind:value={dAnexo}
        hint={anexosEstado === "error"
          ? "No se pudieron cargar los anexos; sin anexo no se puede declarar."
          : "El servidor toma la huella SHA-256 del anexo y la incluye en la constancia."}
      />
    {/if}
    <FormField
      id="exc-totp"
      label={totpObligatorio
        ? "Código de verificación (app autenticadora)"
        : "Código de verificación (app autenticadora), obligatorio si tiene certificado de firma activo"}
      type="text"
      required={totpObligatorio}
      inputmode="numeric"
      autocomplete="one-time-code"
      bind:value={dTotp}
      hint={totpObligatorio
        ? "El servidor exige que usted firme también la constancia."
        : "Obligatorio si tiene un certificado de firma activo; si no lo tiene, déjelo vacío."}
    />
    <label class="exc__confirm">
      <input type="checkbox" bind:checked={dConfirma} />
      <span>
        Entiendo que esta declaración queda sellada y es <strong>irreversible</strong>, salvo que
        otra persona autorizada la revoque; tras revocarla, la declaración puede repetirse.
      </span>
    </label>
    {#if dError}
      <p class="exc__error" role="alert">
        <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
        {dError}
      </p>
    {/if}
  </div>
  <div slot="footer" class="exc__footer">
    <Button variant="secondary" on:click={() => (declararAbierto = false)}>Cancelar</Button>
    <Button
      variant="danger"
      on:click={confirmarDeclarar}
      disabled={!declararListo}
      loading={dEnviando}
    >
      Declarar firma inobtenible
    </Button>
  </div>
</Modal>

<Modal
  open={revocarAbierto}
  title={`Revocar excepción — ${ROL_LABELS[rRol] ?? ""}`}
  size="md"
  on:close={() => (revocarAbierto = false)}
>
  <p>
    La excepción deja de cubrir a esta parte, que podrá firmar de nuevo. La constancia original se
    conserva. La revocación queda sellada (y firmada por usted si tiene certificado activo); solo se
    puede declarar de nuevo cuando esté protegida en el almacén y la parte no haya firmado.
  </p>
  <div class="exc__form">
    <FormField
      id="exc-rev-causa"
      label="Causa de la revocación"
      type="select"
      required
      options={[{ value: "", label: "Elija la causa" }, ...CAUSAS_REVOCACION]}
      bind:value={rCausa}
    />
    <FormField
      id="exc-rev-fundamento"
      label="Fundamento de la revocación"
      type="textarea"
      rows={4}
      required
      bind:value={rFundamento}
      hint={`${AVISO_FUNDAMENTO} Entre ${FUNDAMENTO_MIN} y ${FUNDAMENTO_MAX} caracteres (${rFundamento.trim().length}).`}
    />
    <FormField
      id="exc-rev-totp"
      label={rTotpForzado
        ? "Código de verificación (app autenticadora)"
        : "Código de verificación (app autenticadora), obligatorio si tiene certificado de firma activo"}
      type="text"
      required={rTotpForzado}
      inputmode="numeric"
      autocomplete="one-time-code"
      bind:value={rTotp}
      hint={rTotpForzado
        ? "El servidor exige que usted firme también la revocación."
        : "Obligatorio si tiene un certificado de firma activo; si no lo tiene, déjelo vacío."}
    />
    <label class="exc__confirm">
      <input type="checkbox" bind:checked={rConfirma} />
      <span>Confirmo que quiero revocar esta excepción de firma.</span>
    </label>
    {#if rError}
      <p class="exc__error" role="alert">
        <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
        {rError}
      </p>
    {/if}
  </div>
  <div slot="footer" class="exc__footer">
    <Button variant="secondary" on:click={() => (revocarAbierto = false)}>Cancelar</Button>
    <Button
      variant="danger"
      on:click={confirmarRevocar}
      disabled={!revocarListo}
      loading={rEnviando}
    >
      Revocar excepción
    </Button>
  </div>
</Modal>

<style lang="scss">
  .exc {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);

    &__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-2);
    }

    &__title {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
    }

    &__empty {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }

    &__list {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
    }

    &__item {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
      padding: var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      background: var(--op-surface);

      &--revocada {
        background: var(--op-bg);
      }
    }

    &__item-head {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: var(--op-space-2);
    }

    &__dl {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: var(--op-space-2) var(--op-space-3);
      font-size: var(--op-font-sm);

      dt {
        font-size: var(--op-font-xs);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--op-text-secondary);
      }

      dd {
        color: var(--op-text);
        overflow-wrap: anywhere;
      }
    }

    &__wide {
      grid-column: 1 / -1;
    }

    &__hash {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-xs);
      word-break: break-all;
    }

    &__ord {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__verif {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: var(--op-space-1);
      font-size: var(--op-font-sm);
    }

    &__copias {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__warn {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);

      i {
        color: var(--op-warning);
        margin-right: var(--op-space-1);
      }
    }

    &__form {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      padding: var(--op-space-2) 0;
    }

    &__confirm {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }

    &__error {
      font-size: var(--op-font-sm);
      color: var(--op-error-fg);

      i {
        margin-right: var(--op-space-1);
      }
    }

    &__footer {
      display: flex;
      justify-content: flex-end;
      gap: var(--op-space-2);
    }
  }
</style>
