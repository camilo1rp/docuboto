/* ============================================================================
   Docuboto 3.0 — expedientes, búsqueda, distribución, alertas, capacidades,
   inteligencia, actividad, sistema, panel del agente, paleta y modales
   ========================================================================== */
window.VIEWS = window.VIEWS || {};
(function (V) {
  const esc = window.esc;

  /* ==========================================================================
     EXPEDIENTES
     ========================================================================== */
  V.expedientes = function () {
    return `
      <div class="grid grid--4" style="margin-bottom:var(--db-space-4)">
        <div class="stat"><div class="stat__label">${icon("folder")} Abiertos</div><div class="stat__value">142</div><div class="stat__delta">6 sin movimiento</div></div>
        <div class="stat"><div class="stat__label">${icon("lock")} Con retención legal</div><div class="stat__value">3</div><div class="stat__delta">1 por litigio activo</div></div>
        <div class="stat"><div class="stat__label">${icon("eye")} Publicados</div><div class="stat__value">67</div><div class="stat__delta">Ley 1712 art. 12</div></div>
        <div class="stat"><div class="stat__label">${icon("send")} En transferencia</div><div class="stat__value">8</div><div class="stat__delta">2 esperan firma</div></div>
      </div>

      <div class="bulkbar" style="background:var(--db-surface-2);border-color:var(--db-line);color:var(--db-ink-2)">
        ${icon("info")} La retención se deriva de la serie TRD. Cambiarla no es una edición: crea una versión nueva del instrumento.
        <div class="spacer"></div>
        <button class="btn btn--sm btn--ghost" data-act="go" data-route="capacidades">${icon("layers")} Ver capacidades</button>
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width:34px"><input type="checkbox" class="check" aria-label="Seleccionar todo"></th>
              <th style="min-width:196px">Expediente</th>
              <th style="min-width:180px">Serie y retención</th>
              <th style="min-width:72px">Radicados</th>
              <th style="min-width:140px">Estado</th>
              <th style="min-width:110px">Publicación</th>
              <th style="width:34px"></th>
            </tr>
          </thead>
          <tbody>
            ${DB.expedientes
              .map(
                (x) => `
              <tr>
                <td><input type="checkbox" class="check" aria-label="Seleccionar ${x.code}"></td>
                <td>
                  <div class="cell-main mono nowrap" style="font-size:12.5px">${x.code}</div>
                  <div class="cell-sub">${esc(x.name)}</div>
                </td>
                <td>
                  <div class="cell-main" style="font-weight:400;font-size:12.5px">${esc(x.serie)}</div>
                  <div class="cell-sub mono">${esc(x.retention)}</div>
                </td>
                <td class="num">${x.radicados}</td>
                <td>
                  <span class="chip chip--${x.tone}">${esc(x.status)}</span>
                  ${x.hold ? `<span class="chip chip--danger" style="margin-left:4px">${icon("lock")} Retención legal</span>` : ""}
                </td>
                <td>${x.published ? `<span class="chip chip--brand">${icon("eye")} Público</span>` : `<span class="chip chip--outline">No publicado</span>`}</td>
                <td><button class="btn btn--sm btn--ghost" data-act="toast" data-msg="Detalle del expediente ${x.code}">${icon("right")}</button></td>
              </tr>`,
              )
              .join("")}
          </tbody>
        </table>
      </div>

      <div class="card__foot" style="border:1px solid var(--db-line);border-radius:var(--db-radius-md);margin-top:var(--db-space-3);background:var(--db-surface)">
        ${icon("info")} Cerrar y transferir son actos de custodia: el sistema no ofrece reabrir. El camino es una nueva transferencia formal.
      </div>`;
  };

  /* ==========================================================================
     BÚSQUEDA
     ========================================================================== */
  V.busqueda = function () {
    return `
      <div class="card card--pad" style="margin-bottom:var(--db-space-4)">
        <div class="row-flex" style="gap:8px">
          <div class="omni" style="max-width:none;flex:1;height:42px;background:var(--db-surface)">
            ${icon("search")}
            <span style="flex:1;color:var(--db-ink)">¿qué ha dicho la entidad sobre licencias en predios con queja de ruido?</span>
            <span class="chip chip--ai">${icon("sparkles")} Preguntar</span>
          </div>
          <button class="btn btn--primary btn--lg" data-act="toast" data-msg="Buscando en cuerpo y anexos">${icon("arrow")}</button>
        </div>
        <div class="row-flex" style="margin-top:10px;gap:6px;flex-wrap:wrap">
          <span class="tiny faint">Buscar en:</span>
          <span class="chip chip--brand">Cuerpo y anexos</span>
          <span class="chip chip--outline">Solo metadatos</span>
          <div class="spacer"></div>
          <span class="tiny faint">${icon("lock")} Tu nivel de acceso recorta los resultados sin avisarte</span>
        </div>
      </div>

      <div class="grid grid--main">
        <div style="display:flex;flex-direction:column;gap:var(--db-space-4)">
          <div class="card card--ai card--pad">
            <div class="ai-summary__head" style="margin-bottom:8px">
              ${icon("sparkles")} Respuesta de proboto · 4 fuentes
              <div class="spacer"></div>
              <span class="chip chip--ai">Nivel 2</span>
            </div>
            <div style="font-size:var(--db-text-sm);line-height:1.65">
              En los últimos dos años la entidad resolvió <b>7 solicitudes</b> de licencia sobre predios
              con queja de ruido asociada. En 5 de ellas exigió el certificado de uso del suelo antes
              de agendar la inspección; en 2 lo pidió después y la visita se repitió. El criterio
              constante es pedir el certificado primero.
              <button class="cite" data-act="cite" data-n="1">1</button>
              <button class="cite" data-act="cite" data-n="2">2</button>
              <button class="cite" data-act="cite" data-n="3">3</button>
            </div>
            <div class="msg__foot" style="margin-top:10px">
              <span>${icon("clock")} 1,4 s</span>
              <span>·</span>
              <span>4 documentos de tu nivel de acceso</span>
              <span>·</span>
              <span>sin efecto en el sistema</span>
            </div>
          </div>

          <div class="card">
            <div class="card__head">
              <div class="card__title">${icon("file")} Documentos</div>
              <div class="spacer"></div>
              <span class="tiny faint">8 resultados · ordenados por similitud</span>
            </div>
            <div class="list">
              ${[
                ["E-2026-4102", "Licencia de urbanismo — predio Calle 12 #4-33", "Coincide por predio y por antecedente de queja", "2026", true],
                ["E-2025-3388", "Solicitud de licencia — carrera 8 #22-10", "Mismo tipo documental y misma comuna", "2025", false],
                ["S-2025-2210", "Respuesta a solicitud de licencia con certificado de uso", "Contiene el criterio que se cita en la respuesta", "2025", true],
                ["E-2025-1904", "Queja por ruido — establecimiento comercial", "Texto muy cercano al radicado que estás mirando", "2025", false],
              ]
                .map(
                  ([num, title, why, year, ai]) => `
                <button class="list-item" data-act="toast" data-msg="Abriendo ${num}">
                  <span style="min-width:0;flex:1">
                    <span class="row-flex" style="gap:6px">
                      <span class="mono tiny" style="color:var(--db-ink-2)">${num}</span>
                      ${ai ? `<span class="chip chip--ai">${icon("sparkles")} Parecido</span>` : ""}
                    </span>
                    <span style="display:block;font-weight:600;margin-top:3px;line-height:1.4">${esc(title)}</span>
                    <span style="display:block;font-size:11.5px;color:var(--db-ink-3);margin-top:2px">${esc(why)}</span>
                  </span>
                  <span class="tiny faint">${year}</span>
                </button>`,
                )
                .join("")}
            </div>
          </div>
        </div>

        <aside style="display:flex;flex-direction:column;gap:var(--db-space-3)">
          <div class="card card--pad">
            <div class="tiny strong" style="margin-bottom:9px;letter-spacing:.04em;text-transform:uppercase;color:var(--db-ink-3)">Filtros</div>
            <div class="field" style="margin-bottom:10px">
              <label class="field__label" for="f1">Dependencia</label>
              <select class="select" id="f1"><option>Todas</option><option>Planeación</option></select>
            </div>
            <div class="field" style="margin-bottom:10px">
              <label class="field__label" for="f2">Tipo documental</label>
              <select class="select" id="f2"><option>Todos</option><option>Solicitud de licencia</option></select>
            </div>
            <div class="field">
              <label class="field__label" for="f3">Año</label>
              <select class="select" id="f3"><option>2024 – 2026</option></select>
            </div>
          </div>
          <div class="card card--pad card--sunken">
            <div class="tiny strong" style="margin-bottom:5px">${icon("search")} Las tres búsquedas</div>
            <p class="tiny muted" style="line-height:1.55;margin:0 0 8px">
              <b>Exacta</b> encuentra palabras. <b>Semántica</b> encuentra significado.
              <b>Preguntar</b> te devuelve una respuesta con citas. Ninguna inventa coincidencias.
            </p>
            <div style="display:flex;gap:5px;flex-wrap:wrap">
              <span class="chip chip--outline">Exacta</span>
              <span class="chip chip--outline">Semántica</span>
              <span class="chip chip--ai">Preguntar</span>
            </div>
          </div>
        </aside>
      </div>`;
  };

  /* ==========================================================================
     DISTRIBUCIÓN
     ========================================================================== */
  V.distribucion = function (s) {
    const tab = s.tab || "envios";
    const tabsHtml = `
      <div class="tabs" role="tablist">
        ${[
          ["envios", "Envíos", 3],
          ["correo", "Correo", 12],
          ["canales", "Canales", null],
        ]
          .map(
            ([id, label, count]) =>
              `<button class="tab" aria-selected="${tab === id}" data-act="dist-tab" data-tab="${id}">${label}${
                count != null ? `<span class="tab__count">${count}</span>` : ""
              }</button>`,
          )
          .join("")}
      </div>`;

    const body =
      tab === "canales"
        ? `
        <div class="card">
          <div class="card__head">
            <div class="card__title">${icon("link")} Canales conectados</div>
            <div class="spacer"></div>
            <button class="btn btn--sm" data-act="toast" data-msg="Conectar un canal nuevo">${icon("plus")} Conectar</button>
          </div>
          ${DB.channels
            .map(
              (c) => `
            <div class="chan">
              <span class="chan__icon">${icon(c.icon)}</span>
              <span style="min-width:0;flex:1">
                <span class="chan__name">${esc(c.name)}</span>
                <span class="chan__meta">${esc(c.desc)} · ${esc(c.detail)}</span>
              </span>
              <span class="chan__side">
                <span class="chip chip--${c.tone === "ok" ? "ok" : "outline"}">${esc(c.state)}</span>
                ${c.gated ? `<span class="chip chip--outline">${icon("lock")}</span>` : `<button class="btn btn--sm btn--ghost" data-act="toast" data-msg="Configuración de ${c.name}">${icon("settings")}</button>`}
              </span>
            </div>`,
            )
            .join("")}
        </div>`
        : tab === "correo"
          ? `
        <div class="card">
          <div class="card__head">
            <div class="card__title">${icon("mail")} Correo institucional</div>
            <div class="spacer"></div>
            <span class="chip chip--ok">${icon("check")} Sincronizado hace 2 min</span>
          </div>
          <div class="list">
            ${[
              ["Contraloría Departamental", "Solicitud de información — contrato 2025-014", "Hoy 09:12", "Sin radicar"],
              ["Personería Municipal", "Traslado de queja por presunto acoso laboral", "Hoy 08:40", "Sugerido: reservado"],
              ["Ciudadano — J. Ramírez", "Petición de poda de árbol en espacio público", "Ayer 17:22", "Sin radicar"],
              ["SECOP II", "Notificación de adjudicación — proceso 2026-88", "Ayer 14:05", "Sin radicar"],
            ]
              .map(
                ([from, subj, time, tag]) => `
              <div class="list-item" style="align-items:center">
                <span class="chan__icon" style="width:30px;height:30px">${icon("mail")}</span>
                <span style="min-width:0;flex:1">
                  <span style="display:block;font-weight:600;line-height:1.35">${esc(subj)}</span>
                  <span style="display:block;font-size:11.5px;color:var(--db-ink-3);margin-top:2px">${esc(from)} · ${esc(time)}</span>
                </span>
                <span class="chip ${tag.startsWith("Sugerido") ? "chip--ai" : "chip--outline"}">${esc(tag)}</span>
                <button class="btn btn--sm" data-act="go" data-route="documentos" data-tab="radicar">Radicar</button>
              </div>`,
              )
              .join("")}
          </div>
        </div>`
          : `
        <div class="grid grid--4" style="margin-bottom:var(--db-space-4)">
          <div class="stat"><div class="stat__label">${icon("truck")} En tránsito</div><div class="stat__value">2</div><div class="stat__delta">1 con rastreo vencido</div></div>
          <div class="stat"><div class="stat__label">${icon("check")} Entregados</div><div class="stat__value">148</div><div class="stat__delta">con acuse firmado</div></div>
          <div class="stat"><div class="stat__label">${icon("rotate")} Devueltos</div><div class="stat__value">4</div><div class="stat__delta">dirección errada</div></div>
          <div class="stat stat--ai"><div class="stat__label">${icon("clock")} Tiempo medio</div><div class="stat__value">2,3</div><div class="stat__delta">días hábiles</div></div>
        </div>

        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>Remesa</th><th>Destinatario</th><th>Piezas</th><th>Estado</th><th>Ruta</th><th>Acuse</th><th></th></tr>
            </thead>
            <tbody>
              ${DB.shipments
                .map(
                  (x) => `
                <tr>
                  <td><div class="cell-main mono nowrap" style="font-size:12.5px">${x.track}</div><div class="cell-sub nowrap">${esc(x.operator)} · ${esc(x.guide)}</div></td>
                  <td><div class="cell-main" style="font-weight:400">${esc(x.recipient)}</div><div class="cell-sub">${esc(x.eta)}</div></td>
                  <td class="num">${x.items}</td>
                  <td><span class="chip chip--${x.tone}">${esc(x.state)}</span></td>
                  <td style="min-width:170px">
                    <div class="progress-track" style="margin:0">
                      ${[1, 2, 3, 4]
                        .map(
                          (n) => `
                        <span class="pt-step ${n < x.step ? "pt-step--done" : n === x.step ? "pt-step--now" : ""}" title="Paso ${n}">
                          <span class="pt-step__dot"></span>
                        </span>
                        ${n < 4 ? `<span class="pt-line ${n < x.step ? "pt-line--done" : ""}"></span>` : ""}`,
                        )
                        .join("")}
                    </div>
                  </td>
                  <td>${x.acuse ? `<span class="chip chip--ok">${icon("check")} Firmado</span>` : `<span class="chip chip--outline">Pendiente</span>`}</td>
                  <td>
                    <button class="btn btn--sm btn--ghost" data-act="toast" data-msg="Rastreo de ${x.track}">${icon("external")}</button>
                  </td>
                </tr>`,
                )
                .join("")}
            </tbody>
          </table>
        </div>

        <div class="bulkbar" style="background:var(--db-surface-2);border-color:var(--db-line);color:var(--db-ink-2);margin-top:var(--db-space-3)">
          ${icon("info")} Pasar un envío a «Entregado» exige adjuntar el acuse. Sin acuse no hay entrega, y no se puede deshacer.
        </div>`;
    return `${tabsHtml}${body}`;
  };

  /* ==========================================================================
     ALERTAS
     ========================================================================== */
  V.alertas = function (s) {
    const tab = s.tab || "vencimientos";
    const tabsHtml = `
      <div class="tabs" role="tablist">
        ${[
          ["vencimientos", "Vencimientos", 6],
          ["reglas", "Reglas", 4],
          ["canales", "Canales", null],
        ]
          .map(
            ([id, label, count]) =>
              `<button class="tab" aria-selected="${tab === id}" data-act="alert-tab" data-tab="${id}">${label}${
                count != null ? `<span class="tab__count">${count}</span>` : ""
              }</button>`,
          )
          .join("")}
      </div>`;

    const body =
      tab === "reglas"
        ? `
        <div class="row-flex" style="margin-bottom:var(--db-space-3)">
          <span class="tiny faint">${DB.alertRules.filter((r) => r.active).length} activas · ${DB.alertRules.reduce((a, r) => a + r.fired, 0)} avisos este mes</span>
          <div class="spacer"></div>
          <button class="btn btn--sm btn--primary" data-act="toast" data-msg="Editor de regla abierto">${icon("plus")} Nueva regla</button>
        </div>
        ${DB.alertRules
          .map(
            (r) => `
          <div class="rule-card">
            <div class="rule-card__head">
              <div class="rule-card__sentence">
                Cuando <b>${esc(r.when[0])}</b> ${r.when[1] ? `<b>${esc(r.when[1])}</b>` : ""} ${esc(r.when[2] || "")},
                ${esc(r.then[0])} <b>${esc(r.then[1])}</b>${r.then[2] ? " " + esc(r.then[2]) : ""}.
                <div class="rule-card__meta">
                  <span>${icon("user")} ${esc(r.who)}</span>
                  <span>${icon("send")} ${esc(r.channel)}</span>
                  <span>${icon("zap")} ${r.fired} avisos este mes</span>
                </div>
              </div>
              <button class="switch" role="switch" aria-checked="${r.active}" aria-label="Activar regla"
                      data-act="toggle-rule" data-id="${r.id}"></button>
            </div>
          </div>`,
          )
          .join("")}
        <div class="card__foot" style="border:1px solid var(--db-line);border-radius:var(--db-radius-md);background:var(--db-surface);margin-top:var(--db-space-3)">
          ${icon("undo")} Apagar una regla es reversible: se puede volver a encender sin perder su historial.
        </div>`
        : tab === "canales"
          ? `
        <div class="card">
          <div class="card__head"><div class="card__title">${icon("send")} Por dónde se avisa</div></div>
          ${DB.alertChannels
            .map(
              (c) => `
            <div class="chan">
              <span class="chan__icon">${icon(c.icon)}</span>
              <span style="min-width:0;flex:1">
                <span class="chan__name">${esc(c.name)}</span>
                <span class="chan__meta">${esc(c.desc)}</span>
              </span>
              <span class="chan__side">
                ${
                  c.locked
                    ? `<span class="chip chip--outline">${c.gated ? icon("lock") + " " : ""}${esc(c.state)}</span>`
                    : `<span class="chip chip--ok">${esc(c.state)}</span>
                       <button class="switch" role="switch" aria-checked="true" aria-label="Activar ${c.name}" data-act="toast" data-msg="Canal actualizado"></button>`
                }
              </span>
            </div>`,
            )
            .join("")}
        </div>
        <div class="grid grid--2" style="margin-top:var(--db-space-4)">
          <div class="card card--pad">
            <div class="section-title" style="font-size:var(--db-text-base)">${icon("clock")} Horario de los avisos</div>
            <p class="tiny muted" style="line-height:1.55;margin-bottom:10px">Los avisos respetan el calendario de días hábiles de la entidad y los feriados cargados.</p>
            <div class="form-grid">
              <div class="field"><label class="field__label" for="h1">Resumen diario</label><input class="input" id="h1" value="7:30 a.m."></div>
              <div class="field"><label class="field__label" for="h2">Recordatorio de vencimiento</label><input class="input" id="h2" value="2 días hábiles antes"></div>
            </div>
          </div>
          <div class="card card--pad card--sunken">
            <div class="section-title" style="font-size:var(--db-text-base)">${icon("bell")} Quién recibe qué</div>
            <p class="tiny muted" style="line-height:1.55;margin:0">
              El responsable recibe su vencimiento. La jefatura recibe el escalamiento.
              Nadie recibe el contenido de un radicado reservado: solo el aviso de que existe.
            </p>
          </div>
        </div>`
          : `
        <div class="alert-group">
          ${DB.alertGroups
            .map(
              (g) => `
            <div class="alert-group__head">
              <div class="alert-group__title">
                <span class="dot--${
                  g.tone === "danger" ? "off" : g.tone === "warn" ? "idle" : "live"
                }" style="background:${
                  g.tone === "danger"
                    ? "var(--db-danger)"
                    : g.tone === "warn"
                      ? "var(--db-warn)"
                      : "var(--db-info)"
                }"></span>
                ${esc(g.title)}
              </div>
              <span class="alert-group__count">${g.items.length}</span>
            </div>
            ${g.items
              .map(
                (a) => `
              <div class="alert alert--${a.tone === "danger" ? "overdue" : a.tone === "warn" ? "today" : "soon"}">
                <span class="alert__rule"></span>
                <div style="min-width:0">
                  <div class="alert__title">${esc(a.title)}</div>
                  <div class="alert__meta">
                    <span class="chip chip--outline">${esc(a.rule)}</span>
                    <span>${esc(a.detail)}</span>
                    <span>· ${esc(a.owner)}</span>
                  </div>
                </div>
                <div class="alert__actions">
                  ${
                    a.action === "Escalar"
                      ? `<button class="btn btn--sm btn--danger-ghost" data-act="preflight" data-kind="escalar">${icon("flag")} Escalar</button>`
                      : `<button class="btn btn--sm" data-act="toast" data-msg="Recordatorio enviado">${icon("send")} ${esc(a.action)}</button>`
                  }
                  <button class="btn btn--sm btn--ghost" data-act="snooze-alert" data-id="${a.id}" title="Silenciar 7 días">${icon("clock")}</button>
                  <button class="btn btn--sm btn--ghost" data-act="toast" data-msg="Abrir radicado" title="Abrir">${icon("right")}</button>
                </div>
              </div>`,
              )
              .join("")}`,
            )
            .join("")}
        </div>
        <div class="bulkbar" style="background:var(--db-ai-soft-2);border-color:var(--db-ai-line);color:var(--db-ai-ink)">
          ${icon("sparkles")} proboto detectó que <b>E-2026-4823</b> lleva 3 días sin movimiento. Sugiere recordar al responsable antes de escalar.
          <div class="spacer"></div>
          <button class="btn btn--sm btn--ai" data-act="toast" data-msg="Recordatorio sugerido">${icon("send")} Recordar</button>
        </div>`;
    return `${tabsHtml}${body}`;
  };

  /* ==========================================================================
     CAPACIDADES
     ========================================================================== */
  V.capacidades = function (s) {
    const mode = DB.modes[s.mode];
    const preset = new Set(DB.capsForMode(s.mode));
    const caps = s.caps;
    const custom =
      caps.size === preset.size && [...preset].every((id) => caps.has(id)) ? 0 : 1;

    const groups = [];
    DB.capabilities.forEach((c) => {
      if (!groups.includes(c.group)) groups.push(c.group);
    });

    return `
      <div class="page__head">
        <div class="page__heading">
          <div class="page__eyebrow">${icon("layers")} Gobierno de la instalación</div>
          <h1 class="page__title">Capacidades</h1>
          <p class="page__sub">
            El modo es un punto de partida, no una jaula. Enciende lo que la entidad necesita
            y la interfaz se adapta: aparecen las pantallas, los avisos y las fricciones que
            ese acto exige.
          </p>
        </div>
        <div class="page__actions">
          <span class="chip chip--${custom ? "warn" : "brand"} chip--lg">
            ${icon(custom ? "sliders" : "check")}
            Modo ${mode.name}${custom ? " · con ajustes propios" : ""}
          </span>
          <button class="btn" data-act="toast" data-msg="Historial de cambios de configuración">${icon("clock")} Historial</button>
        </div>
      </div>

      <div class="modes">
        ${Object.values(DB.modes)
          .map(
            (m) => `
          <button class="mode ${s.mode === m.id ? "is-active" : ""}" data-act="mode-pick" data-mode="${m.id}">
            <div class="mode__head">
              <span class="mode__icon">${icon(m.icon)}</span>
              <span class="mode__name">${m.name}</span>
              <span class="mode__tag">${s.mode === m.id ? "Activo" : m.tag}</span>
            </div>
            <div class="mode__desc">${esc(m.desc)}</div>
            <div class="mode__count">${icon("layers")} ${DB.capsForMode(m.id).length} capacidades encendidas</div>
            <div class="mode__for">${icon("users")} ${esc(m.for)}</div>
          </button>`,
          )
          .join("")}
      </div>

      <div class="card card--pad card--ai" style="margin-bottom:var(--db-space-5);display:flex;gap:14px;align-items:flex-start">
        <span class="mode__icon" style="background:var(--db-ai-soft);color:var(--db-ai-ink);flex-shrink:0">${icon("info")}</span>
        <div style="min-width:0">
          <div class="strong" style="margin-bottom:3px">Qué cambia en el modo ${mode.name}</div>
          <div class="small muted" style="line-height:1.55">${esc(mode.note)}</div>
          <div class="row-flex" style="margin-top:8px;gap:6px;flex-wrap:wrap">
            ${
              s.mode === "regulado"
                ? `<span class="chip chip--danger">${icon("lock")} Actos irreversibles con doble control</span>
                   <span class="chip chip--warn">${icon("database")} Auditoría inalterable</span>
                   <span class="chip chip--outline">${icon("shield")} Retención WORM</span>`
                : s.mode === "escala"
                  ? `<span class="chip chip--brand">${icon("flag")} Escalamiento a jefatura</span>
                     <span class="chip chip--brand">${icon("pen")} Firmas en cadena</span>
                     <span class="chip chip--outline">${icon("truck")} Distribución multicanal</span>`
                  : `<span class="chip chip--brand">${icon("check")} Camino corto</span>
                     <span class="chip chip--outline">${icon("undo")} Todo clave se deshace</span>
                     <span class="chip chip--outline">${icon("users")} Un solo nivel de aprobación</span>`
            }
          </div>
        </div>
        <div class="spacer"></div>
        ${
          custom
            ? `<button class="btn btn--sm" data-act="reset-mode">${icon("rotate")} Volver al preset</button>`
            : ""
        }
      </div>

      ${groups
        .map((g) => {
          const items = DB.capabilities.filter((c) => c.group === g);
          const on = items.filter((c) => caps.has(c.id)).length;
          return `
          <div class="cap-group">
            <div class="cap-group__head">
              <span class="cap-group__title">${esc(g)}</span>
              <span class="cap-group__count">${on}/${items.length}</span>
            </div>
            ${items
              .map((c) => {
                const isOn = caps.has(c.id);
                const missing = c.deps.filter((d) => !caps.has(d));
                const locked = missing.length > 0;
                const dependents = DB.capabilities.filter(
                  (x) => x.deps.includes(c.id) && caps.has(x.id),
                );
                const names = missing
                  .map((d) => DB.capabilities.find((x) => x.id === d).name)
                  .join(" y ");
                return `
                <div class="cap ${isOn ? "is-on" : ""} ${locked ? "is-locked" : ""}">
                  <span class="cap__icon">${icon(c.icon)}</span>
                  <div style="min-width:0">
                    <div class="cap__name">
                      ${esc(c.name)}
                      ${c.tiers.regulado && !c.tiers.escala ? `<span class="chip chip--outline">Solo en Regulado</span>` : ""}
                    </div>
                    <div class="cap__desc">${esc(c.desc)}</div>
                    ${
                      locked
                        ? `<div class="cap__deps">${icon("lock")} Requiere <b>${esc(names)}</b></div>`
                        : c.deps.length
                          ? `<div class="cap__deps">${icon("link")} Depende de ${esc(c.deps.map((d) => DB.capabilities.find((x) => x.id === d).name).join(", "))}</div>`
                          : ""
                    }
                    ${
                      isOn && dependents.length
                        ? `<div class="cap__deps">${icon("alert")} Apagarla exige apagar antes ${dependents.length === 1 ? "esta capacidad" : dependents.length + " capacidades"}: ${esc(dependents.length > 2 ? dependents.slice(0, 2).map((d) => d.name).join(", ") + "…" : dependents.map((d) => d.name).join(" y "))}</div>`
                        : ""
                    }
                  </div>
                  <div class="cap__side">
                    ${isOn ? `<span class="chip chip--ok">Activa</span>` : ""}
                    <button class="switch" role="switch" aria-checked="${isOn}"
                      ${locked ? "disabled" : ""}
                      data-act="toggle-cap" data-id="${c.id}"
                      aria-label="${isOn ? "Apagar" : "Encender"} ${c.name}"></button>
                  </div>
                </div>`;
              })
              .join("")}
          </div>`;
        })
        .join("")}

      <div class="grid grid--2" style="margin-top:var(--db-space-5)">
        <div class="card card--pad">
          <div class="section-title" style="font-size:var(--db-text-base)">${icon("lock")} Reglas que no se pueden apagar</div>
          <p class="small muted" style="line-height:1.6;margin:0">
            En cualquier modo: trazabilidad de quién hizo qué, aislamiento entre entidades,
            y el permiso del servidor por encima de lo que la interfaz muestre.
            Son el suelo del producto, no una capacidad.
          </p>
        </div>
        <div class="card card--pad card--sunken">
          <div class="section-title" style="font-size:var(--db-text-base)">${icon("undo")} Deshacer y modos</div>
          <p class="small muted" style="line-height:1.6;margin:0">
            Cambiar de modo es reversible durante 10 segundos. Apagar una capacidad
            <b>no borra datos</b>: la oculta y la deja de evaluar. Volver a encenderla
            la restaura sin reindexar nada.
          </p>
        </div>
      </div>`;
  };

  /* ==========================================================================
     INTELIGENCIA (proboto)
     ========================================================================== */
  V.inteligencia = function (s) {
    const level = s.aiLevel || "triage";
    const activeIdx = DB.aiLevels.findIndex((l) => l.id === level);
    return `
      <div class="page__head">
        <div class="page__heading">
          <div class="page__eyebrow">${icon("sparkles")} Capa de inteligencia</div>
          <h1 class="page__title">proboto</h1>
          <p class="page__sub">
            Un proveedor aparte, dentro de la instalación, que habla con Docuboto solo por
            un protocolo declarado. Docuboto sigue siendo el sistema de registro: la
            inteligencia propone, la persona decide.
          </p>
        </div>
        <div class="page__actions">
          <span class="chip chip--ok chip--lg"><span class="dot--live"></span> Conectado</span>
          <button class="btn" data-act="toast" data-msg="Capacidades declaradas por proboto">${icon("file")} Ver capacidades declaradas</button>
        </div>
      </div>

      <div class="grid grid--main">
        <div style="display:flex;flex-direction:column;gap:var(--db-space-5)">
          <div>
            <div class="section-title">${icon("layers")} Hasta dónde puede llegar</div>
            <p class="section-sub">Cada nivel se apoya en el anterior. Al subir, crece lo que el agente puede hacer y lo que queda escrito de su intervención.</p>
            <div class="ladder">
              ${DB.aiLevels
                .map((l, i) => {
                  const isActive = l.id === level;
                  const isOff = i > activeIdx;
                  const locked = l.gated;
                  return `
                <div class="rung ${isActive ? "is-active" : ""} ${isOff ? "is-off" : ""}">
                  <div class="rung__level">${l.level}</div>
                  <div style="min-width:0">
                    <div class="rung__title">
                      ${esc(l.title)}
                      ${locked ? `<span class="chip chip--outline">${icon("lock")} Requiere revisión</span>` : ""}
                      ${isActive ? `<span class="chip chip--ai">En uso</span>` : ""}
                    </div>
                    <div class="rung__desc">${esc(l.desc)}</div>
                    <div class="rung__gain">${icon("arrow")} ${esc(l.gain)}</div>
                  </div>
                  <div class="rung__side">
                    <button class="btn btn--sm ${isActive ? "btn--soft" : ""}" data-act="ai-level" data-id="${l.id}"
                      ${locked ? "disabled" : ""}>
                      ${isActive ? icon("check") + " Activo" : locked ? icon("lock") + " Bloqueado" : "Activar"}
                    </button>
                  </div>
                </div>`;
                })
                .join("")}
            </div>
          </div>

          <div class="card">
            <div class="card__head">
              <div class="card__title">${icon("eye")} Qué puede salir de la entidad</div>
              <div class="spacer"></div>
              <span class="tiny faint">Por defecto, solo público</span>
            </div>
            <div class="card__body">
              ${DB.aiScopes
                .map(
                  (sc) => `
                <div class="cap" style="border-bottom:1px solid var(--db-line-soft)">
                  <span class="cap__icon">${icon(sc.on ? "unlock" : "lock")}</span>
                  <div>
                    <div class="cap__name">${sc.name}</div>
                    <div class="cap__desc">${esc(sc.note)}</div>
                  </div>
                  <div class="cap__side">
                    <button class="switch" role="switch" aria-checked="${sc.on}"
                      ${sc.forced ? "disabled" : ""} data-act="toggle-scope" data-id="${sc.id}"
                      aria-label="Permitir documentos ${sc.name}"></button>
                  </div>
                </div>`,
                )
                .join("")}
              <div class="bulkbar" style="margin:12px 0 0;background:var(--db-warn-soft);border-color:var(--db-warn-line);color:var(--db-warn)">
                ${icon("warn")} Habilitar <b>Reservada</b> exige una declaración de transmisión y una política de tratamiento publicada.
              </div>
            </div>
          </div>

          <div>
            <div class="section-title">${icon("database")} Actividad del agente</div>
            <p class="section-sub">Todo lo que proboto propuso queda a la vista, con su fuente y su resultado.</p>
            <div class="table-wrap">
              <table class="table">
                <thead><tr><th>Cuándo</th><th>Propuesta</th><th>Sobre</th><th>Resultado</th><th>Persona</th></tr></thead>
                <tbody>
                  ${[
                    ["Hoy 08:42", "Tipificación + reparto", "E-2026-4821", "Aplicada", "Camilo Romero"],
                    ["Hoy 08:15", "Posible duplicado", "E-2026-4824", "Pendiente", "—"],
                    ["Ayer 16:40", "Plazo por vencer", "S-2026-0912", "Descartada", "Laura Restrepo"],
                    ["Ayer 11:02", "Antecedente relacionado", "E-2026-4823", "Aplicada", "Diana Pardo"],
                  ]
                    .map(
                      ([t, p, o, r, u]) => `
                    <tr>
                      <td class="muted" style="white-space:nowrap">${t}</td>
                      <td>${esc(p)}</td>
                      <td class="mono" style="font-size:12.5px">${o}</td>
                      <td><span class="chip chip--${r === "Aplicada" ? "ok" : r === "Pendiente" ? "warn" : "outline"}">${r}</span></td>
                      <td class="muted">${esc(u)}</td>
                    </tr>`,
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <aside style="display:flex;flex-direction:column;gap:var(--db-space-3)">
          <div class="card card--pad">
            <div class="tiny strong" style="margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em;color:var(--db-ink-3)">Lo que proboto nunca hace</div>
            <div style="display:flex;flex-direction:column;gap:9px;font-size:var(--db-text-sm);line-height:1.5">
              ${[
                ["lock", "No escribe sin una persona"],
                ["shield", "No recibe credenciales de la API"],
                ["eye", "No ve un nivel que la entidad no habilitó"],
                ["database", "No guarda copia propia de los documentos"],
              ]
                .map(
                  ([ic, t]) => `<div class="row-flex" style="align-items:flex-start;gap:8px">
                    <span style="color:var(--db-brand-ink);margin-top:2px">${icon(ic)}</span>
                    <span>${esc(t)}</span>
                  </div>`,
                )
                .join("")}
            </div>
          </div>

          <div class="card card--pad">
            <div class="tiny strong" style="margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em;color:var(--db-ink-3)">Proveedor declarado</div>
            <div class="dl" style="grid-template-columns:1fr;gap:9px">
              <div><dt>Conectado por</dt><dd>PPI v1 · declaración firmada</dd></div>
              <div><dt>Corre en</dt><dd>Infraestructura de la entidad</dd></div>
              <div><dt>Retención de datos</dt><dd>Ninguna · sin copia</dd></div>
              <div><dt>Última verificación</dt><dd>Hoy 06:00 · firma válida</dd></div>
            </div>
          </div>

          <div class="card card--pad card--ai">
            <div class="tiny strong" style="margin-bottom:5px">${icon("info")} Con el interruptor apagado</div>
            <p class="tiny muted" style="line-height:1.55;margin:0">
              Docuboto no hace ni una llamada al proveedor. Buscar y radicar siguen funcionando:
              la búsqueda exacta no depende de la inteligencia.
            </p>
          </div>
        </aside>
      </div>`;
  };

  /* ==========================================================================
     ACTIVIDAD
     ========================================================================== */
  V.actividad = function (s) {
    const filter = s.ledgerFilter || "todo";
    const rows = DB.activity.filter(
      (l) =>
        filter === "todo" ||
        (filter === "a" && l.cls === "A") ||
        (filter === "b" && l.cls === "B") ||
        (filter === "c" && l.cls === "C"),
    );
    const count = (c) => DB.activity.filter((l) => l.cls === c).length;

    return `
      <div class="page__head">
        <div class="page__heading">
          <div class="page__eyebrow">${icon("undo")} Reversibilidad</div>
          <h1 class="page__title">Actividad</h1>
          <p class="page__sub">
            Cada acto deja asiento. La pregunta no es «¿se puede borrar?» sino
            «¿qué hace falta para dejarlo sin efecto?». Aquí está la respuesta, acto por acto.
          </p>
        </div>
        <div class="page__actions">
          <button class="btn" data-act="toast" data-msg="Exportar el libro de actos">${icon("download")} Exportar</button>
        </div>
      </div>

      <div class="grid grid--3" style="margin-bottom:var(--db-space-4)">
        <div class="stat" style="border-color:var(--db-undo-line)">
          <div class="stat__label">${icon("undo")} Se deshacen</div>
          <div class="stat__value" style="color:var(--db-undo)">${count("A")}</div>
          <div class="stat__delta">Durante 10 s, desde la propia pantalla</div>
        </div>
        <div class="stat" style="border-color:var(--db-warn-line)">
          <div class="stat__label">${icon("rotate")} Se revierten</div>
          <div class="stat__value" style="color:var(--db-warn)">${count("B")}</div>
          <div class="stat__delta">Con un acto contrario y su motivo</div>
        </div>
        <div class="stat">
          <div class="stat__label">${icon("lock")} No se deshacen</div>
          <div class="stat__value">${count("C")}</div>
          <div class="stat__delta">Se corrigen con otro acto jurídico</div>
        </div>
      </div>

      <div class="row-flex" style="margin-bottom:var(--db-space-3);flex-wrap:wrap">
        ${[
          ["todo", "Todo"],
          ["a", "Deshacer"],
          ["b", "Revertir"],
          ["c", "No reversible"],
        ]
          .map(
            ([id, label]) =>
              `<button class="chip chip--lg ${
                filter === id ? "chip--brand" : "chip--outline"
              }" data-act="ledger-filter" data-id="${id}" style="cursor:pointer">${label}</button>`,
          )
          .join("")}
        <div class="spacer"></div>
        <span class="tiny faint">${rows.length} actos · últimos 7 días</span>
      </div>

      <div class="card card--pad">
        <div class="ledger">
          ${rows
            .map(
              (l) => `
            <div class="ledger__item">
              <span class="ledger__badge ledger__badge--${
                l.cls === "A" ? "undo" : l.cls === "B" ? "revert" : "final"
              }">${icon(l.icon)}</span>
              <div style="min-width:0">
                <div class="ledger__title">${esc(l.title)}</div>
                <div class="ledger__meta">
                  <span>${esc(l.meta)}</span>
                  ${
                    l.cls === "C"
                      ? `<span class="chip chip--outline">${icon("lock")} No reversible</span>`
                      : ""
                  }
                </div>
                <div class="ledger__diff">${esc(l.diff)}</div>
              </div>
              <div class="ledger__action">
                ${
                  l.cls === "A"
                    ? `<button class="btn btn--sm" data-act="undo-entry" data-id="${l.id}">${icon("undo")} Deshacer</button>`
                    : l.cls === "B"
                      ? `<button class="btn btn--sm btn--warn-ghost" data-act="preflight" data-kind="revertir" data-id="${l.id}">${icon("rotate")} Revertir…</button>`
                      : `<button class="btn btn--sm btn--ghost" data-act="toast" data-msg="Constancia de este acto">${icon("external")} Ver constancia</button>`
                }
              </div>
            </div>`,
            )
            .join("")}
        </div>
      </div>

      <div class="grid grid--3" style="margin-top:var(--db-space-4)">
        ${[
          ["undo", "Deshacer", "Devuelve el estado anterior y deja los dos asientos. Sin diálogo, con una barra de 10 segundos."],
          ["rotate", "Revertir", "No borra: crea el acto contrario. Exige motivo y, a veces, aprobación de otra persona."],
          ["lock", "No reversible", "El acto ya produjo un efecto jurídico. Se corrige con anulación, adenda o una nueva transferencia."],
        ]
          .map(
            ([ic, t, d]) => `
          <div class="card card--pad">
            <div class="row-flex" style="gap:7px;margin-bottom:4px">
              <span class="ledger__badge ledger__badge--${ic === "undo" ? "undo" : ic === "rotate" ? "revert" : "final"}">${icon(ic)}</span>
              <b style="font-family:var(--db-font-display)">${t}</b>
            </div>
            <p class="tiny muted" style="line-height:1.55;margin:0">${d}</p>
          </div>`,
          )
          .join("")}
      </div>`;
  };

  /* ==========================================================================
     SISTEMA (tokens y componentes)
     ========================================================================== */
  V.sistema = function () {
    const swatches = [
      ["Superficie", "--db-canvas", "#f0f5fc"],
      ["Blanco", "--db-surface", "#ffffff"],
      ["Noche", "--db-ink", "#0a1122"],
      ["Txt 2", "--db-ink-2", "#4a5163"],
      ["Línea", "--db-line", "#dde1e9"],
      ["Azul", "--db-brand", "#095ece"],
      ["Azul oscuro", "--db-brand-ink-hover", "#0749a3"],
      ["Tinte", "--db-brand-soft", "#e3edfb"],
      ["Cian", "--db-ai", "#00d4e0"],
      ["Cian profundo", "--db-ai-ink", "#066169"],
      ["Éxito", "--db-ok", "#0b7a4b"],
      ["Aviso", "--db-warn", "#9a5b00"],
      ["Peligro", "--db-danger", "#b3261e"],
      ["Reversible", "--db-undo", "#095ece"],
    ];
    return `
      <div class="page">
        <div class="page__head">
          <div class="page__heading">
            <div class="page__eyebrow">${icon("grid")} Sistema de diseño 3.0</div>
            <h1 class="page__title">Sistema</h1>
            <p class="page__sub">
              Una capa nueva sobre los tokens que ya existen. Los nombres <code class="mono">--op-*</code>
              se conservan como alias, así que ninguna pantalla actual se rompe al adoptarla.
            </p>
          </div>
          <div class="page__actions">
            <button class="btn" data-act="theme-toggle">${icon("moon")} Cambiar tema</button>
            <button class="btn" data-act="density-toggle">${icon("sliders")} Densidad</button>
          </div>
        </div>

        <div class="section-title">${icon("layers")} Color</div>
        <p class="section-sub">Paleta Proboto: azul #095ECE, cian #00D4E0, noche #0A1122, superficie #F0F5FC. El cian solo aparece cuando algo lo propuso proboto.</p>
        <div class="card card--pad" style="margin-bottom:var(--db-space-5)">
          <div class="swatches">
            ${swatches
              .map(
                ([name, v, hex]) => `
              <div>
                <div class="swatch__chip" style="background:var(${v})"></div>
                <div class="swatch__name">${name}</div>
                <div class="swatch__val">${v}</div>
              </div>`,
              )
              .join("")}
          </div>
        </div>

        <div class="grid grid--2" style="margin-bottom:var(--db-space-5)">
          <div class="card card--pad">
            <div class="section-title">${icon("file")} Tipografía</div>
            <div class="specimen">
              <div class="specimen__row"><span class="specimen__tag">Display 40/700</span><span style="font-family:var(--db-font-display);font-size:32px;font-weight:700;letter-spacing:-0.03em">Documentos, claros</span></div>
              <div class="specimen__row"><span class="specimen__tag">Título 24/700</span><span style="font-family:var(--db-font-display);font-size:24px;font-weight:700;letter-spacing:-0.025em">Bandeja de entrada</span></div>
              <div class="specimen__row"><span class="specimen__tag">Cuerpo 14.5/400</span><span style="font-size:14.5px">Public Sans para todo lo que se lee de corrido y se opera.</span></div>
              <div class="specimen__row"><span class="specimen__tag">Mono 12/500</span><span class="mono">E-2026-4821</span></div>
            </div>
          </div>
          <div class="card card--pad">
            <div class="section-title">${icon("sliders")} Espaciado y forma</div>
            <div style="display:flex;gap:8px;align-items:flex-end;margin:14px 0">
              ${[4, 8, 12, 16, 24, 32]
                .map(
                  (n) =>
                    `<span style="width:${n}px;height:24px;background:var(--db-brand-soft);border:1px solid var(--db-brand-line);border-radius:3px"></span>`,
                )
                .join("")}
            </div>
            <div style="display:flex;gap:8px;align-items:center">
              ${[6, 8, 12, 16, 22]
                .map(
                  (n) =>
                    `<span style="width:46px;height:34px;border:1px solid var(--db-line-strong);border-radius:${n}px;background:var(--db-surface-2)"></span>`,
                )
                .join("")}
            </div>
            <p class="tiny muted" style="margin-top:12px;line-height:1.55">
              Base de 4 px. Radios 6/8/12/16/22. La separación la hace la línea, no la sombra.
            </p>
          </div>
        </div>

        <div class="section-title">${icon("grid")} Componentes</div>
        <p class="section-sub">Botones, chips, campos, estados y el vocabulario de reversibilidad.</p>
        <div class="card card--pad" style="margin-bottom:var(--db-space-5)">
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
            <button class="btn btn--primary">${icon("check")} Acción principal</button>
            <button class="btn">Acción secundaria</button>
            <button class="btn btn--soft">${icon("sparkles")} Con IA</button>
            <button class="btn btn--ghost">Terciaria</button>
            <button class="btn btn--danger-ghost">${icon("lock")} Irreversible</button>
            <button class="btn" disabled>Deshabilitada</button>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px">
            <span class="chip chip--brand">En trámite</span>
            <span class="chip chip--ok">${icon("check")} Cerrado</span>
            <span class="chip chip--warn">${icon("clock")} Vence hoy</span>
            <span class="chip chip--danger">${icon("alert")} Vencido</span>
            <span class="chip chip--ai">${icon("sparkles")} proboto</span>
            <span class="chip chip--undo">${icon("undo")} Deshacer</span>
            <span class="chip chip--outline">Neutro</span>
          </div>
          <div class="form-grid" style="margin-bottom:16px">
            <div class="field"><label class="field__label">Campo</label><input class="input" placeholder="Escribe algo…"></div>
            <div class="field field--ai"><label class="field__label">Campo con propuesta <span class="field__ai-tag">proboto</span></label><input class="input" value="Planeación · Inspección"></div>
          </div>
          <div class="consequence" style="max-width:520px;margin:0">
            <div class="consequence__row consequence__row--undo">${icon("undo")}<span><b>Deshacer</b> — se revierte solo, con barra de 10 s.</span></div>
            <div class="consequence__row consequence__row--warn">${icon("rotate")}<span><b>Revertir</b> — exige motivo y deja constancia.</span></div>
            <div class="consequence__row consequence__row--danger">${icon("lock")}<span><b>No reversible</b> — se corrige con otro acto jurídico.</span></div>
          </div>
        </div>
      </div>`;
  };

  /* ==========================================================================
     PANEL DEL AGENTE
     ========================================================================== */
  V.agent = function (s) {
    const tab = s.agentTab || "brief";
    const level = DB.aiLevels.find((l) => l.id === (s.aiLevel || "triage"));

    const body =
      tab === "activity"
        ? `
        <div>
          <div class="agent__label">${icon("undo")} Lo que hice y puedes deshacer</div>
          ${DB.activity
            .filter((l) => l.undoable)
            .map(
              (l) => `
            <div class="ledger__item">
              <span class="ledger__badge ledger__badge--undo">${icon(l.icon)}</span>
              <div style="min-width:0">
                <div class="ledger__title">${esc(l.title)}</div>
                <div class="ledger__meta">${esc(l.meta)}</div>
              </div>
              <button class="btn btn--sm" data-act="undo-entry" data-id="${l.id}">${icon("undo")}</button>
            </div>`,
            )
            .join("")}
        </div>`
        : tab === "chat"
          ? `
        <div class="msg--user">¿Qué antecedentes hay de licencias en predios con queja de ruido?</div>
        <div class="msg--agent">
          <span class="agent__avatar"><img src="assets/proboto-mark.svg" alt=""></span>
          <div class="msg__body">
            <p>Encontré <b>7 solicitudes</b> con ese patrón en los últimos dos años. En cinco se pidió el
            certificado de uso del suelo antes de agendar la inspección; en dos se pidió después y la
            visita se repitió. <button class="cite" data-act="cite" data-n="1">1</button><button class="cite" data-act="cite" data-n="2">2</button></p>
            <p>El criterio constante está en la respuesta <span class="mono">S-2025-2210</span>, que cita el
            artículo 3 del acuerdo municipal. <button class="cite" data-act="cite" data-n="3">3</button></p>
            <div class="evidence" style="margin-top:9px">
              <div class="evidence__head">${icon("file")} E-2025-2210 · pág. 2</div>
              <div class="evidence__quote">«…se requiere certificado de uso del suelo previo a la inspección ocular, conforme al Acuerdo 014 de 2022.»</div>
            </div>
            <div class="msg__foot">
              <span class="chip chip--ai">3 fuentes</span>
              <span>sin efecto en el sistema</span>
            </div>
          </div>
        </div>
        <div class="msg--user">Redáctame el oficio para pedir el certificado al solicitante.</div>
        <div class="msg--agent">
          <span class="agent__avatar"><img src="assets/proboto-mark.svg" alt=""></span>
          <div class="msg__body">
            <p>Puedo dejarlo como borrador citado, a tu nombre, para que lo revises. No lo radica:
            el número lo gastas tú.</p>
            <div class="sugg__actions" style="padding:8px 0 0">
              <button class="btn btn--sm btn--ai" data-act="toast" data-msg="Borrador creado con 3 citas">${icon("file")} Crear borrador</button>
              <button class="btn btn--sm btn--ghost" data-act="toast" data-msg="Fuentes de la redacción">${icon("eye")} Ver fuentes</button>
            </div>
          </div>
        </div>`
          : `
        <div>
          <div class="agent__label">
            ${icon("sparkles")} Sobre la mesa
            <span class="spacer"></span>
            <span class="chip chip--ai">en vivo</span>
          </div>
          <div class="brief">
            <div class="brief__lead">
              Revisé lo que llegó desde las 6:00. <b>Tres cosas</b> necesitan una decisión tuya
              antes de las 5:00 p. m.
            </div>
            <button class="brief__item" data-act="agent-open" data-agent-tab="chat">
              <span class="brief__n brief__n--danger">1</span>
              <span><b>E-2026-4819</b> venció ayer. Es un recurso con término perentorio.</span>
              <span class="brief__arrow">${icon("right")}</span>
            </button>
            <button class="brief__item" data-act="agent-open" data-agent-tab="chat">
              <span class="brief__n brief__n--warn">2</span>
              <span><b>S-2026-0912</b> espera tu visto bueno; vence a las 17:00.</span>
              <span class="brief__arrow">${icon("right")}</span>
            </button>
            <button class="brief__item" data-act="agent-open" data-agent-tab="chat">
              <span class="brief__n brief__n--ai">3</span>
              <span><b>E-2026-4824</b> parece duplicado de E-2026-4798.</span>
              <span class="brief__arrow">${icon("right")}</span>
            </button>
          </div>
        </div>

        <div>
          <div class="agent__label">
            ${icon("layers")} Sugerencias
            <span class="spacer"></span>
            <span class="tiny faint">${Object.keys(DB.triage).length} pendientes</span>
          </div>
          ${["r1", "r8"]
            .map((id) => {
              const r = DB.records.find((x) => x.id === id);
              const t = DB.triage[id];
              const applied = s.appliedSuggestions && s.appliedSuggestions.has(id);
              return `
            <div class="sugg" style="margin-bottom:8px">
              <div class="sugg__head">
                ${icon("sparkles")} ${esc(t.kind)}
                <span class="spacer"></span>
                <span class="sugg__kind">${t.confidence} fuentes</span>
              </div>
              <div class="sugg__title">${esc(t.title)}</div>
              <div class="sugg__why">
                ${esc(t.why)} <button class="cite" data-act="cite" data-n="1">1</button>
              </div>
              <dl class="sugg__fields">
                ${t.fields
                  .slice(0, 3)
                  .map(
                    ([k, v]) => `<div class="sugg__field"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`,
                  )
                  .join("")}
              </dl>
              <div class="sugg__actions">
                <button class="btn btn--sm btn--ai" data-act="apply-suggestion" data-id="${id}" ${applied ? "disabled" : ""}>
                  ${icon("check")} ${applied ? "Aplicada" : "Aplicar"}
                </button>
                <button class="btn btn--sm" data-act="reject-suggestion" data-id="${id}">${icon("x")} Descartar</button>
                <button class="btn btn--sm btn--ghost" data-act="open-record" data-id="${id}">${icon("right")}</button>
              </div>
            </div>`;
            })
            .join("")}
        </div>

        <div class="agent__trust">
          ${icon("lock")}
          <span>proboto propone, no ejecuta. Cada aplicación pasa por el permiso de la persona y queda en el libro de actos.</span>
        </div>`;

    return `
      <div class="agent__head">
        <span class="agent__avatar"><img src="assets/proboto-mark.svg" alt=""></span>
        <div class="agent__id">
          <span class="agent__name">proboto</span>
          <span class="agent__mode"><span class="dot--live"></span> ${esc(level.title)}</span>
        </div>
        <div class="spacer"></div>
        <button class="icon-btn" data-act="agent-close" title="Cerrar panel">${icon("x")}</button>
      </div>
      <div class="agent__tabs" role="tablist">
        ${[
          ["brief", "Resumen"],
          ["chat", "Preguntar"],
          ["activity", "Actividad"],
        ]
          .map(
            ([id, label]) =>
              `<button class="agent__tab" aria-selected="${tab === id}" data-act="agent-tab" data-tab="${id}">${label}</button>`,
          )
          .join("")}
      </div>
      <div class="agent__body">${body}</div>
      <div class="agent__composer">
        <div class="composer">
          <textarea placeholder="Pregunta sobre tus documentos…" rows="1" data-act="noop"></textarea>
          <div class="composer__row">
            <span class="composer__scope">${icon("building")} Mi dependencia</span>
            <span class="composer__scope">${icon("file")} Este radicado</span>
            <button class="composer__send" title="Enviar" data-act="toast" data-msg="proboto está leyendo…">${icon("arrow")}</button>
          </div>
        </div>
        <div class="agent__trust">
          ${icon("shield")}
          <span>Solo lee los niveles que la entidad habilitó. Hoy: <b>público</b>.</span>
        </div>
      </div>`;
  };

  /* ==========================================================================
     PALETA DE COMANDOS
     ========================================================================== */
  V.palette = function (s) {
    const mode = s.paletteMode || "preguntar";
    return `
      <div class="palette-overlay" data-act="palette-close">
        <div class="palette" data-act="noop" role="dialog" aria-modal="true" aria-label="Buscador y comandos">
          <div class="palette__input-row">
            ${icon(mode === "preguntar" ? "sparkles" : mode === "hacer" ? "zap" : "search")}
            <input class="palette__input" autofocus
              placeholder="${mode === "preguntar" ? "Pregunta lo que necesites…" : mode === "hacer" ? "¿Qué quieres hacer?" : "Buscar documentos, personas o números…"}"
              data-act="noop">
            <span class="kbd">esc</span>
          </div>
          <div class="palette__modes" role="tablist">
            ${[
              ["preguntar", "Preguntar", "sparkles"],
              ["buscar", "Buscar", "search"],
              ["hacer", "Hacer", "zap"],
            ]
              .map(
                ([id, label, ic]) =>
                  `<button class="palette__mode" role="tab" aria-selected="${mode === id}" data-act="palette-mode" data-id="${id}">${icon(ic)} ${label}</button>`,
              )
              .join("")}
            <span class="spacer"></span>
            <span class="tiny faint" style="align-self:center">⌘K</span>
          </div>
          <div class="palette__body">
            ${
              mode === "preguntar"
                ? `
              <div class="palette__group">Respuesta</div>
              <div class="palette__answer">
                <b>E-2026-4819</b> venció ayer. Es un recurso de reposición con término perentorio:
                si no hay actuación hoy, el silencio puede favorecer al recurrente.
                <button class="cite" data-act="cite" data-n="1">1</button>
                <div class="msg__foot" style="margin-top:6px"><span class="chip chip--ai">2 fuentes</span><span>sin efecto</span></div>
              </div>
              <div class="palette__group">También preguntan</div>
              ${["¿Qué vence esta semana y no tiene responsable?", "¿Cuántos derechos de petición vencidos hay en 2026?", "Resumen del expediente EXP-2026-0091"]
                .map(
                  (q) =>
                    `<button class="palette__item" data-act="toast" data-msg="Preguntando…"><span class="ico-wrap" style="color:var(--db-ai-ink)">${icon("sparkles")}</span><span class="palette__item-main">${esc(q)}</span></button>`,
                )
                .join("")}`
                : mode === "hacer"
                  ? `
              <div class="palette__group">Acciones</div>
              ${[
                ["Radicar un documento", "Entrada, salida o interno", "file"],
                ["Preparar una remesa", "3 pendientes de despacho", "truck"],
                ["Asignar radicados a una dependencia", "6 sin responsable", "user"],
                ["Cerrar un expediente", "Irreversible · pide motivo", "lock"],
                ["Crear una regla de alerta", "Cuando algo lleve N días sin movimiento", "bell"],
              ]
                .map(
                  ([t, sub, ic]) =>
                    `<button class="palette__item" data-act="palette-action" data-msg="${esc(t)}">
                      ${icon(ic)}
                      <span class="palette__item-main"><b>${esc(t)}</b><span class="palette__item-sub">${esc(sub)}</span></span>
                      ${ic === "lock" ? `<span class="chip chip--outline">${icon("lock")}</span>` : ""}
                    </button>`,
                )
                .join("")}`
                  : `
              <div class="palette__group">Documentos</div>
              ${DB.records
                .slice(0, 3)
                .map(
                  (r) => `
                <button class="palette__item" data-act="open-record" data-id="${r.id}">
                  ${icon("file")}
                  <span class="palette__item-main"><b>${esc(r.subject)}</b><span class="palette__item-sub">${r.num} · ${esc(r.from)}</span></span>
                  <span class="chip chip--${r.statusChip}">${esc(r.statusLabel)}</span>
                </button>`,
                )
                .join("")}
              <div class="palette__group">Personas y dependencias</div>
              ${[
                ["Diana Pardo", "Gobierno y Convivencia", "user"],
                ["Laura Restrepo", "Secretaría General", "user"],
                ["Inspección de Planeación", "Planeación", "building"],
              ]
                .map(
                  ([n, d, ic]) =>
                    `<button class="palette__item" data-act="toast" data-msg="Abriendo ${esc(n)}">
                      ${icon(ic)}
                      <span class="palette__item-main"><b>${esc(n)}</b><span class="palette__item-sub">${esc(d)}</span></span>
                    </button>`,
                )
                .join("")}`
            }
          </div>
          <div class="palette__foot">
            <span><span class="kbd">↑</span><span class="kbd">↓</span> navegar</span>
            <span><span class="kbd">↵</span> abrir</span>
            <span><span class="kbd">⌘</span><span class="kbd">↵</span> con IA</span>
            <span class="spacer"></span>
            <span>${icon("lock")} respeta tu nivel de acceso</span>
          </div>
        </div>
      </div>`;
  };

  /* ==========================================================================
     MODALES
     ========================================================================== */
  V.modal = function (s) {
    const m = s.modal;
    if (!m) return "";

    /* — Cambio de modo — */
    if (m.type === "mode") {
      const target = DB.modes[m.mode];
      const current = new Set(s.caps);
      const next = new Set(DB.capsForMode(m.mode));
      const added = [...next].filter((id) => !current.has(id));
      const removed = [...current].filter((id) => !next.has(id));
      const name = (id) => DB.capabilities.find((c) => c.id === id).name;
      const strict = m.mode === "regulado";
      return `
        <div class="overlay" data-act="modal-close">
          <div class="modal" data-act="noop" role="dialog" aria-modal="true" aria-labelledby="mode-title">
            <div class="modal__head">
              <span class="modal__icon ${strict ? "modal__icon--danger" : "modal__icon--ai"}">${icon(strict ? "shield" : target.icon)}</span>
              <div style="min-width:0">
                <div class="modal__title" id="mode-title">Pasar al modo ${target.name}</div>
                <div class="modal__sub">${esc(target.desc)}</div>
              </div>
            </div>
            <div class="modal__body">
              <div class="consequence">
                ${
                  added.length
                    ? `<div class="consequence__row consequence__row--ok">${icon("plus")}<span><b>${added.length} capacidades se encienden.</b> ${added.slice(0, 5).map(name).map(esc).join(", ")}${added.length > 5 ? "…" : ""}</span></div>`
                    : ""
                }
                ${
                  removed.length
                    ? `<div class="consequence__row consequence__row--warn">${icon("minus")}<span><b>${removed.length} capacidades se apagan.</b> No se borra ningún dato; dejan de mostrarse y de evaluarse.</span></div>`
                    : ""
                }
                ${
                  strict
                    ? `<div class="consequence__row consequence__row--danger">${icon("lock")}<span><b>Los actos irreversibles pasan a exigir dos personas.</b> Finalizar, anular, desclasificar y eliminar dejan de poderse ejecutar en solitario.</span></div>
                       <div class="consequence__row">${icon("database")}<span>La auditoría pasa a ser inalterable: los asientos no se pueden corregir, solo compensar.</span></div>
                       <div class="consequence__row">${icon("clock")}<span>Las series TRD deben estar convalidadas antes de cerrar el primer expediente.</span></div>`
                    : `<div class="consequence__row">${icon("unlock")}<span>Los actos irreversibles siguen pidiendo motivo escrito, pero no segunda firma.</span></div>`
                }
              </div>
              <label class="check" style="margin-top:14px">
                <input type="checkbox" id="mode-ack">
                <span>Entiendo que este cambio aplica a toda la entidad desde que se guarde,
                y que puedo deshacerlo durante 10 segundos.</span>
              </label>
            </div>
            <div class="modal__foot">
              <button class="btn btn--ghost" data-act="modal-close">Cancelar</button>
              <button class="btn btn--primary" data-act="mode-apply" data-mode="${m.mode}">
                ${icon(strict ? "shield" : "check")} Activar modo ${target.name}
              </button>
            </div>
          </div>
        </div>`;
    }

    /* — Pre-vuelo de una acción — */
    if (m.type === "preflight") {
      const kind = m.kind;
      const isTramitar = kind === "tramitar";
      const isDevolver = kind === "devolver";
      const isEscalar = kind === "escalar";
      const isRevertir = kind === "revertir";

      const title = isTramitar
        ? "Marcar el radicado en trámite"
        : isDevolver
          ? "Devolver el radicado"
          : isEscalar
            ? "Escalar la alerta"
            : isRevertir
              ? "Revertir un acto"
              : "Confirmar la acción";

      const reversible = isTramitar || isDevolver;

      return `
        <div class="overlay" data-act="modal-close">
          <div class="modal" data-act="noop" role="dialog" aria-modal="true" aria-labelledby="pf-title">
            <div class="modal__head">
              <span class="modal__icon ${reversible ? "modal__icon--undo" : "modal__icon--danger"}">${icon(reversible ? "undo" : "lock")}</span>
              <div style="min-width:0">
                <div class="modal__title" id="pf-title">${title}</div>
                <div class="modal__sub">Esto es lo que va a pasar, antes de que pase.</div>
              </div>
            </div>
            <div class="modal__body">
              <div class="consequence">
                ${
                  isTramitar
                    ? `<div class="consequence__row">${icon("user")}<span>El radicado pasa a <b>En trámite</b> y el responsable actual sigues siendo tú.</span></div>
                       <div class="consequence__row">${icon("clock")}<span>El plazo no cambia: vence <b>hoy a las 18:00</b>.</span></div>
                       <div class="consequence__row consequence__row--undo">${icon("undo")}<span><b>Se puede deshacer.</b> Durante 10 segundos desde la barra inferior. Después queda el asiento, y volver atrás sería un acto nuevo.</span></div>`
                    : isDevolver
                      ? `<div class="consequence__row">${icon("user")}<span>Vuelve a <b>Ventanilla única</b> y se avisa a quien lo radicó.</span></div>
                         <div class="consequence__row consequence__row--warn">${icon("rotate")}<span><b>Se revierte con un traslado nuevo.</b> La devolución queda asentada con su motivo y no se borra.</span></div>
                         <div class="consequence__row">${icon("alert")}<span>El motivo es obligatorio y queda en el historial del radicado.</span></div>`
                      : isEscalar
                        ? `<div class="consequence__row">${icon("flag")}<span>Sube la prioridad y avisa al jefe de dependencia.</span></div>
                           <div class="consequence__row consequence__row--warn">${icon("rotate")}<span>Se revierte bajando la prioridad, con un acto registrado.</span></div>`
                        : `<div class="consequence__row consequence__row--warn">${icon("rotate")}<span>No se borra el acto anterior: se crea el acto contrario.</span></div>
                           <div class="consequence__row">${icon("file")}<span>Exige un motivo que queda en el libro de actos.</span></div>
                           ${s.mode === "regulado" ? `<div class="consequence__row consequence__row--danger">${icon("users")}<span><b>Doble control:</b> otra persona con permiso debe aprobar la reversión.</span></div>` : ""}`
                }
              </div>
              <div class="field" style="margin-top:4px">
                <label class="field__label" for="motivo">Motivo ${reversible && isTramitar ? "(opcional)" : "(obligatorio)"}</label>
                <textarea class="textarea" id="motivo" placeholder="Escribe por qué…" ${
                  reversible && isTramitar ? "" : ""
                }></textarea>
                <span class="field__hint">Queda en el historial y lo lee quien audite.</span>
              </div>
            </div>
            <div class="modal__foot">
              <button class="btn btn--ghost" data-act="modal-close">Cancelar</button>
              <button class="btn ${reversible ? "" : "btn--danger"}" data-act="preflight-confirm" data-kind="${kind}">
                ${icon(reversible ? "check" : "lock")} ${reversible ? "Confirmar y seguir" : "Confirmar"}
              </button>
            </div>
          </div>
        </div>`;
    }

    /* — Constancia / por qué no se puede deshacer — */
    if (m.type === "locked") {
      return `
        <div class="overlay" data-act="modal-close">
          <div class="modal" data-act="noop" role="dialog" aria-modal="true" aria-labelledby="lk-title">
            <div class="modal__head">
              <span class="modal__icon modal__icon--danger">${icon("lock")}</span>
              <div>
                <div class="modal__title" id="lk-title">Este acto no se deshace</div>
                <div class="modal__sub">No es una limitación de la interfaz: es el efecto jurídico del acto.</div>
              </div>
            </div>
            <div class="modal__body">
              <div class="consequence">
                <div class="consequence__row consequence__row--danger">${icon("lock")}<span>El radicado <b>S-2026-0908</b> quedó <b>cerrado</b>. La ubicación se congeló y el número se conserva.</span></div>
                <div class="consequence__row">${icon("file")}<span><b>Lo que sí existe:</b> una <b>solicitud de anulación</b> con causal y motivo, que decide otra persona. No devuelve el radicado a trámite; lo deja <b>anulado</b>, y eso también es definitivo.</span></div>
                <div class="consequence__row">${icon("database")}<span>Ambos actos —el cierre y su corrección— quedan en el libro, encadenados.</span></div>
              </div>
            </div>
            <div class="modal__foot">
              <button class="btn btn--ghost" data-act="modal-close">Entendido</button>
              <button class="btn" data-act="preflight" data-kind="anular">${icon("file")} Solicitar anulación</button>
            </div>
          </div>
        </div>`;
    }

    /* — Cita / evidencia — */
    if (m.type === "cite") {
      const ev = (DB.evidence.r1 || [])[0];
      return `
        <div class="overlay" data-act="modal-close">
          <div class="modal" data-act="noop" role="dialog" aria-modal="true" aria-labelledby="ct-title">
            <div class="modal__head">
              <span class="modal__icon modal__icon--ai">${icon("file")}</span>
              <div>
                <div class="modal__title" id="ct-title">Fuente ${m.n || 1}</div>
                <div class="modal__sub">${ev.doc} · ${ev.page}</div>
              </div>
            </div>
            <div class="modal__body">
              <div class="evidence" style="border-left-width:3px">
                <div class="evidence__head">${icon("file")} Extracto</div>
                <div class="evidence__quote" style="font-size:var(--db-text-sm)">${esc(ev.quote)}</div>
              </div>
              <p class="tiny muted" style="margin-top:12px;line-height:1.55">
                Esta cita se abre desde el documento original. proboto no parafrasea aquí:
                el extracto es literal, para que puedas verificarlo sin salir de la pantalla.
              </p>
            </div>
            <div class="modal__foot">
              <button class="btn btn--ghost" data-act="modal-close">Cerrar</button>
              <button class="btn" data-act="toast" data-msg="Abriendo el documento en la página citada">${icon("external")} Abrir en el documento</button>
            </div>
          </div>
        </div>`;
    }
    return "";
  };
})(window.VIEWS);
