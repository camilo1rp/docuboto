# Intelligence Provider Protocol (PPI)

> **Resumen en español.** PPI es el protocolo neutro entre un sistema de registro (el
> *anfitrión*, como Docuboto) y un proveedor de inteligencia que corre como programa
> aparte. El anfitrión empuja registros y anexos, pregunta en nombre de sus usuarios y
> recibe recomendaciones citadas que una persona acepta o rechaza; el proveedor nunca
> cambia el estado del anfitrión. Esta carpeta forma parte de Docuboto y se licencia,
> como todo el repositorio, bajo **AGPL-3.0-or-later**.
> La decisión de arquitectura que la motiva es el ADR-045 de Docuboto, propuesto.

**Status: draft, version 1.1.0, not yet implemented by any host or provider.**

## Purpose

PPI lets a records system use an intelligence provider without either one depending on
the other's code, database, storage or event bus. The two run as separate programs and
talk only over HTTP:

| Capability | Direction | What it does |
| --- | --- | --- |
| `capabilities` | host → provider | A statement, signed by the provider, of the sensitivity levels it accepts and how each level's model calls are routed, with its subprocessors |
| `connection` | host → provider | The host declares its ordered sensitivity levels, its vocabularies (document types, units), its callback URLs and callback keys |
| `ingest` | host → provider | The host pushes records and attachments, and deletes them. It can also purge everything, or only some levels, and list what the provider holds |
| `query` | host → provider | Search and ask on behalf of a host user, with that user's clearance and an optional scope (a record and its children) |
| `run` | host → provider | On-demand tasks, such as drafting a reply. Results come back as recommendations |
| `recommend` | provider → host (callback) | Typed, cited, advisory recommendations: `classify`, `route`, `deadline`, `relate`, `link_party`, `draft`. An explicit "none" with a reason is also allowed |
| `decisions` | host → provider | What the host's people decided about each recommendation, so the provider can learn |

`recommend` and `decisions` are the two legs of one loop: the provider recommends through
a host callback, and the host answers when a person decides. A second host callback,
`content`, returns the bytes behind an attachment locator.

## What is in this directory

| File | Content |
| --- | --- |
| `README.md` | This file: purpose, rules, versioning, where it lives, conformance and license |
| `ppi-v1.openapi.yaml` | OpenAPI 3.1 description of PPI v1: provider endpoints, host callbacks (`webhooks`) and components |
| `schemas/*.schema.json` | JSON Schema (2020-12) for the core objects: `record`, `attachment`, `citation`, `recommendation`, `decision`, plus `common` |
| `examples/valid/`, `examples/invalid/` | Conformance fixtures |
| `examples/manifest.json` | The fixture index: which schema each fixture is checked against and whether it must validate |

Only YAML, JSON and Markdown files live here, plus `LICENSE`. Schema `$id`s use the
reserved domain `spec.ppi.example`. They are identifiers, not locations.

## Normative rules

The key words MUST, MUST NOT and SHOULD are used as in RFC 2119. The schemas encode what
a single message can show. These rules cover what one message can't.

### Both sides

1. **The host decides what leaves.** A host MUST NOT be obliged to send anything because a
   provider accepts it. Each host chooses which of its levels it transmits.
2. **Idempotency.** Upserts are idempotent per `(external_ref, version)`. Recommendations
   are idempotent per `(provider, ref)`. Decisions are idempotent per
   `(recommendation_ref, decision)`.
3. **Versioning header.** Every request and response carries `PPI-Version`.

### Provider

1. A provider MUST reject an `ingest` item whose `sensitivity` it doesn't accept
   (`level_not_accepted`), and MUST store nothing about it.
2. A deletion, a `410` from `content`, or a purge (of the whole tenant, or of the levels it
   names) MUST remove the object, its children when asked, and everything derived from
   them (parsed text, chunks, embeddings and cached bytes). The provider MUST stop serving the object before it answers.
3. A provider MUST NOT make a claim without at least one citation. When the evidence isn't
   enough, the answer MUST be `insufficient_evidence` with no claims.
4. A recommendation MUST carry at least one citation, and MUST use only vocabulary codes
   the host declared. A `deadline` recommendation is always labelled `computed: true`,
   because the host's value is the official one.
5. A provider MUST verify the SHA-256 of bytes fetched by locator, and MUST fail closed on
   a mismatch. It keeps fetched bytes only while a processing stage needs them.
6. A provider MUST map the pair (token issuer, client ID) to exactly one host tenant.
   `on_behalf_of` and `clearance` MUST NOT widen what that tenant's service account can
   reach.
7. A provider MUST sign `capabilities` and every callback, and MUST NOT send a callback to
   a URL other than the ones the host declared.
8. Recommendations are advisory. A provider MUST NOT change host state by any route.

### Host

1. A host MUST re-check every search hit and citation against its own access rules at the
   moment it shows them, and MUST drop what fails. A claim left with no citations is
   dropped. An answer left with no claims is shown as `insufficient_evidence`.
2. A host MUST verify callback signatures. It MUST reject a call whose timestamp is more
   than 300 s away from its own clock, or whose nonce it has already seen inside that
   window.
3. A host MUST let a person decide every recommendation, and MUST apply an accepted one
   as that person.
4. A host SHOULD stop transmitting when the provider's `capabilities` statement changes,
   until an administrator accepts the change.

## Security model

- **Host → provider.** The host sends an OAuth 2.0 bearer token that it obtains from its
  own identity provider with the client-credentials grant, with one client per host
  tenant. The provider registers each client by issuer and client ID. The host never
  presents that token to anything else.
- **Provider → host.** Callbacks carry an HMAC-SHA256 signature over the method, the
  path, a timestamp, a single-use nonce and the SHA-256 of the body. The host chooses the
  keys and declares them, write-only, in its connection. Two keys may be declared at once
  so they can be rotated. `ppi-v1.openapi.yaml` (`providerSignature`) gives the exact
  string to sign.
- **Capabilities** are signed with the provider's asymmetric key: a detached JWS over the
  RFC 8785 canonical JSON of the statement. The host pins that key in its own
  configuration.
- **Users** travel as a pseudonymous `on_behalf_of` that only the host can map back to a
  person.

## Versioning (semver)

The protocol version is `info.version` in `ppi-v1.openapi.yaml`, and every message
carries it in `PPI-Version`.

- **Patch (1.0.x):** wording, examples or fixtures. Nothing on the wire changes.
- **Minor (1.x.0):** additive and backward compatible. Examples: a new optional field, a
  new endpoint, a new optional query parameter, or a new `Problem.code`. Receivers MUST
  ignore unknown optional fields in objects that allow them.
- **Major (x.0.0):** anything else. Examples: a new required field, a removed field or
  endpoint, a changed meaning, a stricter limit, or a new value in a closed enum. Closed
  enums include `kind`, `decision`, `direction` and `mode`, because peers act on those
  values.

Peers with the same major version interoperate. A new major version gets a new URL
prefix (`/v2`) and both prefixes are served during the migration.

### Changes

- **1.1.0 (2026-10-27).** `PurgeRequest` gains an optional `levels` list, so a host can
  delete only the levels it stops transmitting. Without it, a purge still deletes
  everything for the host tenant. Fixtures: `purge-request-levels.json` (valid) and
  `purge-request-empty-levels.json` (invalid).

## Where the specification lives

- **This directory is the specification.** It is part of the Docuboto repository at
  `docs/contratos/ppi/`. Changes go through a merge request that updates `info.version`
  and the fixtures.
- **A provider implements the protocol in its own code.** It talks to the host over HTTP
  and doesn't need to include these files in its product.
- **The digest** identifies one version of this directory, so a peer can record exactly
  which version it was tested against. It is the SHA-256 of the sorted list of per-file
  SHA-256 sums, computed from inside this directory:

  ```bash
  find . -type f | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256
  ```

  On Linux, `sha256sum` gives the same output as `shasum -a 256`.

## Conformance

A conforming implementation, host or provider, meets three conditions:

1. **Schemas.** Every message it sends validates against this specification. Every
   fixture in `examples/manifest.json` gives the stated result against its schema:
   `valid: true` fixtures validate and `valid: false` fixtures don't. Each invalid
   fixture says why.
2. **Rules.** Its tests cover the normative rules above that apply to its side.
3. **Version.** It sends `PPI-Version`, and refuses a peer with a different major version.

To check the fixtures, load every `schemas/*.schema.json` and the OpenAPI document into a
JSON Schema 2020-12 registry under the base `https://spec.ppi.example/v1/`. Then validate
each fixture against `{"$ref": "<base><schema>"}`, where `<schema>` is the manifest entry,
for example `schemas/record.schema.json` or
`ppi-v1.openapi.yaml#/components/schemas/IngestBatch`.

Fixture data is invented. It describes no real person, organisation or record.

## License and contributions

Copyright (C) 2026 Docuboto Contributors.

This specification is part of Docuboto and is licensed, like the rest of the repository,
under the GNU Affero General Public License, version 3 or (at your option) any later
version. See `LICENSE` and `NOTICE` at the repository root. JSON files can't carry a
license header; this README states the license for every file in this directory.

Files here MUST NOT contain code from any provider. The protocol itself (the OpenAPI
document, the schemas and the fixtures) uses generic vocabulary: no host table, stream or
service names, and no vendor names.
