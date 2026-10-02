# Security Audit

This page records the findings of the weekly dependency audit (the `dependency-audit` CI job).

## Current status

No high or critical vulnerabilities on record.

> This page is updated automatically via Merge Request when the scheduled audit detects HIGH or CRITICAL vulnerabilities in the project's Python dependencies.

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
