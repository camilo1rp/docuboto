# Security Audit

This page records the findings of the weekly dependency audit (the `dependency-audit` CI job).

## Current status

**No run of the dependency audit is recorded on this page.** The sentence "no high or critical
vulnerabilities" that used to stand here was backed by no artifact: the `dependency-audit` job was
fail-open (`pip-audit ... || true`, a missing report was counted as "0 packages", and the
decision to fail was made by a language model). Since 2026-10-03 the audit is deterministic and
fail-closed (`scripts/audit_umbral.py`: a missing or corrupt report is a failure, and so is any
vulnerability with a fix available), covers every `requirements*.lock`, runs on the weekly
schedule and on every merge request, and `npm audit --omit=dev --audit-level=high` over the
frontend is added to every pipeline. Images are scanned with Trivy before being published. **The
date and pipeline of the last clean run are written by the job itself when it finishes
(`audit.json` is kept as an artifact); until the first run with this change, the state is "no run
on record".**

> This page is updated via Merge Request from the scheduled job: it drafts the report from `audit.json`, but **does not decide** whether the job fails (the threshold does).

## Second-pass audit (September 2026)

Besides the automatic dependency review, in September 2026 a manual second-pass audit was carried
out over fourteen axes the earlier reviews had not covered. The twelve previous audits had
concentrated on per-endpoint access control and on the integrity of the audit log; this one looked at
the perimeter, trust between services, data-level isolation, the public surface, file uploads,
cryptography, outbound network calls, the frontend, document parsers, internal messaging, the
assistant, infrastructure, logs and business logic.

### Method

Every finding went through independent reviewers whose task was to **refute** it, and only what survived
that review is recorded. Every fix is validated by restoring the defect and checking that the
corresponding test fails: a test that cannot tell the correct code from the defective code covers
nothing.

### Result

81 findings were confirmed: 6 high severity, 30 medium and 45 low. 42 were refuted. All went through
adversarial review; none is left unverified. **All 81 are fixed**: the six high, the thirty medium and
the forty-five low.

The areas with the most findings were deployment infrastructure, the unauthenticated public surface and
the business logic of the signature circuit. Four findings affect only the development environment, not
the production configuration.

### Behaviour change in the citizen window

The legacy PQRS registration route now goes through the single window, as its own contract already
declared. That subjects it to the entity's decisions: the document type must be declared admissible and
there must be a department that receives what is submitted from outside.

**Before upgrading**, each entity must check that it has declared at least one document type admissible
at the window and one receiving department. Without them the route answers "window not enabled" instead
of admitting the registration. It is the correct behaviour —until now that door admitted documents at
entities that had not enabled online registration, leaving them outside every department's inbox— but
it is a visible change.

## Closure of the 2 October 2026 audit (batch T3)

A user holding the registration permission in read-only mode can no longer see the subject of
a reserved radicado through a third party's radicado list or the responses list, and those
subjects stop travelling to the assistant's external provider. Reply drafts inherit the
classification of the radicado they answer: they cannot be listed, read or edited without the
required level, and editing them leaves an audit entry. The declarative signature of an
attachment requires edit level, takes the hash from the server (not the client) and writes its
audit entry in the same operation.

## Closure of the 2 October 2026 audit (batch T4)

A radicado that failed midway no longer burns a number from the official series. An import
package can no longer move the sequence with another entity's numbers or overflow it (which
used to take a whole year's registration down). Dispatching a shipment or changing its status
by hand requires full edit level, leaves an audit entry naming the person and no longer
overwrites a final state if the postal operator got there first. Importing and exporting
packages require the same level, no longer freeze the service and run one at a time.

## Closure of the 2 October 2026 audit (batch T5)

A webhook no longer carries the reservation cause or legal basis, nor the number and
dependency route of reserved items: anything that does not declare its classification goes
out minimal. Opening or closing an outbound channel requires full edit level and leaves an
audit entry naming the person; delivery is pinned to the address that was validated. The
review chain and the route sheet of a radicado already deleted by retention respect the
caller's access level.

## Closure of the 2 October 2026 audit (batch T6)

A single person can no longer propose, endorse and execute the destruction of a batch:
endorsing and executing require level 4 and different people (whoever proposes does not
endorse, whoever endorses does not execute), checked in the statement itself and before the
first deletion. Batch transitions are atomic with their audit entry, nothing is executed
without the record's retention lock, and the public batch door no longer reveals which
institutions the installation hosts. The signed electronic index stops sealing what the
linker types: radicado number, typology, producing dependency, format, size and hash are
resolved by the server and a mismatching declaration is rejected. Who lent or returned a
physical unit is stored by identifier, not by login. **Open**: the physical loan with no
classification control (AREA-01-13) and propagating a later re-typing to the index.

## Closure of the 2 October 2026 audit (batch T16, documentation)

No security behaviour changed, and that is why it matters: what was fixed is what **the documentation
claimed** and the code already contradicted. Two unsafe recipes are no longer written down (a
`SET search_path` with the slug interpolated unvalidated and unquoted, in `multi-tenancy.md` and
ADR-002: whoever copied the example opened a `search_path` injection), institution onboarding is
documented through its only real route (the gateway does not expose `/api/v1/tenants`), and the
findings about writes without a gate and the per-IP limiter are closed with their evidence.
**The truth is told about webhooks (AREA-14-10)**: the SSRF hardening is implemented (validation on
creation and on every delivery, DNS TOCTOU closed), but **deliveries are not recorded durably** —only
`logger.warning`—; an administrator who subscribes an external URL with no `event_types` lets all
unclassified content out and today only rotatable logs keep a trace. The append-only
`webhook_entregas` table is still pending. Accepted limiter residuals: invalid tokens are not cached
(one IP can force up to 1200 validations a minute against auth-service) and the limiter fails open
without Redis. A guardian (`shared/tests/test_la_documentacion_dice_lo_que_hay.py`) ties each of these
sentences to the code fact that would make it false.

## Webhook secret encrypted at rest

The key used to sign each webhook delivery is no longer stored in clear: whoever reads the
database or a backup cannot forge deliveries the receiver would accept. It is encrypted with
a custody key owned by the service (outside the database, versioned and rotatable), bound to the
tenant and the webhook; the service decrypts it in memory only to sign and stops delivering any
row that is still in clear or cannot be decrypted. It is never returned in a response nor written
to logs or audit entries.

## Closure of the 2 October 2026 audit (batch T2)

Administering users, groups and permissions can no longer be done with the read-only
permission: whoever received it only to consult the audit log could create users and grant
themselves permissions. Nor can anyone join a group with a higher access level to reserved
documents than their own. The access token is validated more strictly (type and issuing
client), the gateway's validation cache is now sealed and reports how long the token has
left, audit-chain verification no longer reveals to an unauthorised profile how many
classified entries exist, and removing another person's second factor is always recorded in
the same operation.

## Closure of the 2 October 2026 audit (batch T1)

The entity's institutional seal and the immutability lock (WORM) are no longer reachable with
a user's session: only the archive service applies them, with an internal credential. Before,
any user with the signing permission could stamp the entity's seal on an arbitrary document,
and one with the expedientes permission could take an unrelated expediente's irreversible lock
or set a litigation hold with no act to lift it. The litigation hold is now declared and lifted
through its own act, with a mandatory motive and an audit entry.

### tenant-service and the owner role (October 2026, T7)

Migration `008` exempted tenant-service from the application role because it was "not routed by the gateway"; it is. As owner, the append-only guard of `audit_log` did not bind it. Fixed: it runs as `DB_APP_USER`, `POST /tenants` runs no DDL, and create/status change leave an atomic audit entry. A guard test requires the application role in both compose files for every gateway-routed service.

### Closure of the 2 October 2026 audit (batch T8)

The second factor and the credential are no longer checked against someone other than the signer: the OTP went to the bearer's mailbox, the TOTP was verified against the bearer's device and the personal key was the bearer's, while the signature was attributed to `X-User-Id`. The old comment called it "fail-safe" and it was the opposite (reachable from a compromised internal service, not from outside). auth-service now says **whose** each factor is and signature-service requires it to match (`403 identidad_incoherente`, audited, before any factor is requested). Someone who already has a TOTP does not drop to email on level ≥ 2 acts. The fingerprint of radicado/expediente chains is not chosen by the client, nor does the signer choose the bytes at signing time where the chain fixes none. The native signature HMAC and the OTP HMAC use per-entity, per-purpose HKDF keys with a message binding entity and object, and `key_version` keeps earlier rows verifiable. `/verify` and `GET /firmas/{id}` apply no-read-up. **Pending (the entity's decision, or external)**: enabling `FIRMA_PERSONAL_REQUIRE_XADES` (classified acts can still be signed with the native HMAC while the gate is `off`; there is now a startup warning and an audit entry if the credentials channel is down), the revocation-reason vocabulary and the HSM/KMS.

### Closure of the 2 October 2026 audit (batch T9)

The failed-event queue (`evento_dead_letter`) is **one physical table** written by workflow, signature, notification and knowledge in the same entity schema, and its primary key was `event_id` alone. When one event exhausted its retries in two consumers (database down, entity without tables, malformed envelope), the second `INSERT … ON CONFLICT DO NOTHING` did nothing: that group left no row and no `evento.dead_letter` entry, its administrator could not see it (the listing filters by group) and the message was already acknowledged in Redis. Traceability was lost (Ley 594/2000, arts. 4 and 19) with no error; it is not exploitable with a token and does not break isolation, hence "medium". The key is now `(origin_group, event_id)` (an idempotent migration in all four services, applicable in any order) and a `shared/tests` guard rejects any `ON CONFLICT` on that table that omits the group. knowledge-service had no way to inspect or replay its queue: a parked `purged`/`annulled` left the fragment searchable with its text forever. It now has the same surface as the others (`PERM_DLQ_ADMIN`, writes at level 3, no-read-up on the live level, atomic audit entry). Also: the consumer name is per replica (was fixed), an orphaned dead-letter keeps the cause of the last failure (was empty), `X-Truncated` no longer fires at exactly 1000 rows and `/health/ready` shows the DLQ and the last sweep. **Pending (operations decision)**: Prometheus/alerts; moving the clearance filter into the `WHERE` and keyset pagination for the listing (beyond 1000 pending rows the oldest stay outside the window).



### Closure of the 2 October 2026 audit (batch T10)

What the assistant confirms on behalf of a person entered the gateway through a domain route and was
recorded as channel `api`, like a direct call: what a model proposed could not be told apart, nor the entry
tied to its turn. Now every mcp-server call carries a signed mark (bound to the entity and the token, 120 s
lifetime) that the gateway verifies before recording `mcp`; without a valid signature the channel is `api`,
and an `mcp` entry stores the `request_id` and the `jti` of the proposal the person approved. The assistant
also flags as "not verified" any radicado number in its answer that no query result returned (before, "do not
invent numbers" was one line of the prompt), forwards the signed real IP (its calls no longer share one
container's per-IP bucket across all entities) and measures each turn. In the knowledge layer, `ollama_local`
is no longer accepted as local by name: with a public `OLLAMA_URL` RAG let reserved and classified chunks out
and only warned in the log; it now excludes them. RAG suggestions get human acceptance (only whoever generated
it, atomic with its entry) and the false "per-entity opt-in" promise is withdrawn. **Verified not to apply**:
expediente reclassification does not exist (its level cannot change after creation), so there is nothing to
propagate to the index; a guardian makes that falsifiable. **Pending (product decision)**: enabling the
knowledge layer per entity, whose `false` default would stop today's ingestion. **Residual**: channel `mcp`
only shows up in services already rebuilt with the new shared library.

### Closure of the 2 October 2026 audit (batch T11)

The frontend proxies' session guard was tested on a hand-picked sample (21 of 219) and nothing asserted the
token cookie flags (`httpOnly`, `sameSite`, `secure`): changing one would make the token readable by
JavaScript or cross-site without anyone noticing. A tree-derived guardian now walks every `+server.js` of the
private area (290 verbs), calls each without a session with body, network and cookies blocked, and requires
401; a test pins the flags in production and development. Forcing the URL of an `/admin` area without its
permission ends at `/403` (the required permission is each area's real one, and an undeclared area stays
closed); this is a **UI mirror**, the backend still re-validates every call. **No prior escalation**: a proxy
without the guard forwarded without a token and the gateway answered 401. **Residual**: the new e2e
(restricted profile and axe) are written but do not run in CI yet.

### Closure of the 2 October 2026 audit (batch T12, archive and storage)

- **Annex deduplication was an existence oracle (AREA-08-09, medium).** Uploading content that already existed as
  another user's annex returned `200` with the foreign row —its `id` and original `filename`, which for a reserved
  annex usually reveals the subject—; anyone guessing a filled-in template confirmed it existed. Deduplication is now
  for the **same author** only (in the query, repeated in the service): another author gets their own row (`201`),
  and with no author (public window, system) there is no deduplication. Orphan purge keeps its protection
  (`dedup_hit_at`) for the same-author case.
- **The AIP replica attested `ok` over a copy nobody looked at and leaked the store's text (AREA-08-04, medium).**
  Retention and the SHA-256 of what was copied are now re-read, a secondary equal to the primary is labelled
  `local`, and the reason reaching the PREMIS event and `audit_log` is the exception type only (+ S3 code), not
  `str(exc)` (host, bucket, request-id).
- **Handover-record immutability had no privilege layer (AREA-07-08, low).** Since the app role stopped being the
  owner it cannot disable the trigger, but it kept `UPDATE`/`DELETE` on the record and its personal signatures:
  the trigger was the only barrier. Migration `054` removes them (same role-existence guard as `008`); tested with
  the real `orpycamcp_app` role: it fails on privileges, not on the trigger.
- **Personal signatures of the record had no Object-Lock (AREA-07-10, medium; technical part only).** The XML of the
  signatures of whoever drew up, handed over and received lived in the documents bucket with no retention. It now
  enters the WORM store (post-commit, best-effort, with reconciliation). **Closed on 2026-10-04**: the
  `firma_inobtenible` act (`POST /acta/firma-inobtenible`, `USUA_PERM_ADMIN` ≥ 3, immutable record `059` + atomic entry, `firmada_con_excepcion` state) and the CT renewal of these signatures (`060`). **Remediation 2026-10-09 (ADR-030, `061`)**: the record was unsigned and unprotected and its support was free text (high); it is now a canonical XML with an XAdES-T seal and the declarant's personal signature per the entity policy, Object-Lock with CT renewal and a server-resolved support; the declarant cannot be any of the three parties (service and engine); the exception is revocable (append-only row); `fundamento` is only shown to `USUA_PERM_ADMIN` ≥ 3; and `sellada_con_excepcion` avoids calling a record with no personal signatures "signed".
- **Accumulated holdings without WORM (AREA-06-04, medium).** Their index, record and AIP stayed without Object-Lock
  indefinitely (correct fail-closed, but permanent). They are now wired with the final extreme date and a clamp
  (at least `today + 400 days`). **Risk to confirm**: an irreversible COMPLIANCE of ~400 days over documentation
  whose schedule has already expired (original D4.4 said "set no retention"); the user decides.
- **Not security, but it closes evidence**: the index is not sealed if it does not validate against its XSD; a transfer
  is not prepared with an unsigned index and its record points to the signature covering the delivered index.
- **Residual**: the `objeto` `CHECK` (055) exempts rows older than its date (a bare `NOT VALID` would have left
  in-flight transfers unable to advance). The exhausted-index email has no consumer of its own in
  notification-service.

### Closure of the 2 October 2026 audit (batch T13, document-service)

- **A classified record could end up with no stated basis and the "never NULL" promise had no backing (AREA-09-04,
  medium).** The API write paths already required the legal basis, but the import accepted a reserved level with a
  null basis and the database did not prevent it. Now the import rejects the whole package and a database constraint
  (validated, with an explicit fill for legacy rows) closes any other entry; the art. 20 index also shows the
  responsible department and who registered it.
- **A radicado's level change was published after the commit and silently lost (AREA-09-05, medium).** If Redis went
  down right then, the level stayed raised while the semantic index kept serving the fragment at the old level. The
  event is now written in the **same transaction** as the change and its audit entry (outbox) and a relay re-sends
  it; likewise for creation and annulment. **Pending (product decision)**: double control for declassification.
  **Closed in B10**: knowledge gates `search`/`get_pivot_chunk` with `GREATEST(fragment level, current radicado level)` in the
  `WHERE`, and an hourly sweep (`reconciliacion_nivel`, raise-only) reconciles the fragment level.
- **Publishing ex officio disclosed the first attachment and did not distinguish what came from outside (AREA-07-12,
  medium, downgraded from high: it required classification authority).** CMIS now only serves attachments marked by
  the publication act, and anything that came through the public window or is typed as admitted there (third-party
  data, Law 1581) cannot be published ex officio.
- **Public intake without automation brake or safe retry (AREA-02-07, medium).** A trap field rejects programs that
  fill in the whole form before a number is consumed, and an idempotency key makes a retry (or a double click)
  return the same radicado with a single sequence number, also under concurrency. **Pending (user decision)**: the
  CAPTCHA provider (reCAPTCHA contradicts the declared sovereignty).
- **No proof of personal-data authorisation (AREA-09-08, medium).** The single window and PQRS now require the
  authorisation and store the policy version, instant and **declared** (unverified) IP in the radicado and its audit
  entry. **Residual**: an IP verified by trusted hops needs the gateway to inject it on public routes; the policy
  text is supplied by the entity.
- **Postal webhook secret in clear, single and non-rotatable (AREA-09-10, medium).** There are now two encrypted
  slots with zero-downtime rotation (the previous one expires) and an admin endpoint that neither returns nor audits
  the secret; manually delivering a shipment requires the delivery receipt. **Residual**: the alert for returned
  mail in notification was not built.
- **Minimum radicado data (AREA-09-02, medium) and a single window with no pre-flight check (AREA-09-07, low):** see
  the changelog (`T13`).
