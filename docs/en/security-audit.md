# Security Audit

This page records the findings of the weekly dependency audit (the `dependency-audit` CI job).

## Current status

No high or critical vulnerabilities on record.

> This page is updated automatically via Merge Request when the scheduled audit detects HIGH or CRITICAL vulnerabilities in the project's Python dependencies.

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
