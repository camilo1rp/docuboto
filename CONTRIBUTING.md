# Contributing to OrpycaMCP

Thank you for considering a contribution to OrpycaMCP! This document outlines our development workflow, code standards, and submission process.

## Code of Conduct

- Be respectful and constructive
- Assume good intent
- Welcome diverse perspectives
- No harassment or discrimination

## Development Setup

All development happens inside Docker. **Never** install dependencies on your host machine.

```bash
git clone <repo-url>
cd orfeoMcp

# Start all services
docker compose up -d

# Or run tests in a specific service
docker compose exec document-service pytest tests/ -v
```

## Workflow

1. **Branch naming**: Feature branches use kebab-case prefixed with the service/area:
   ```
   document/radicacion-validation
   auth/keycloak-realm-sync
   infra/docker-registry-setup
   ```

2. **Commit messages**: Follow [Conventional Commits](https://www.conventionalcommits.org/):
   ```
   feat(document): add validation for sender_entity
   fix(storage): correct SHA-256 checksum on large files
   docs(api): update radicado endpoint schema
   refactor(auth): extract JWT validation to core/security.py
   test(tenant): add integration test for schema provisioning
   ```

3. **Before every commit**, update documentation:
   - `docs/es/api.md` — if you changed an endpoint or schema
   - `docs/es/getting-started.md` — if you added a user-visible feature
   - `CHANGELOG.md` and `docs/es/changelog.md` — entry per commit in Keep a Changelog format
   - `docs/es/roadmap.md` — mark completed items with `[x]`
   - `docs/adr/` — if the change is architecture-level, create or update an ADR

4. **Code quality gates** (enforced before merge):
   - `ruff check` — no import issues, style violations
   - `mypy app/` — type checking passes
   - `pytest tests/ -v` — all tests pass
   - No hardcoded secrets (.env files, API keys, credentials)

## Testing

All tests run in Docker to match the production environment:

```bash
docker compose build <service-name>
docker compose run --rm --no-deps <service-name> pytest -q
```

Or use the unified script:

```bash
./scripts/lint.sh <service-name>  # Linting only
```

> **Never bind-mount the repository read-write into a container.** Docker runs
> as UID 0 and a bind-mount does not translate UIDs, so anything the container
> writes — `__pycache__`, `.pytest_cache`, `.mypy_cache`, generated files —
> lands on your host owned by `root:root` and can no longer be edited or
> formatted without `sudo`. The commands above avoid it: the service image
> already contains the source, and `docker-compose.yml` only mounts config
> read-only. If you must mount the repo for a one-off, either mount it `:ro` or
> pass `--user "$(id -u):$(id -g)"`.

### Test structure

- One test file per router: `tests/test_<resource>.py`
- Use `conftest.py` for shared fixtures (mock clients, test data)
- Async tests require `@pytest.mark.asyncio` and `pytest-asyncio` plugin
- Mock external services (Keycloak, MinIO, SMTP) — no external dependencies in tests

## Code Standards

### Python

- Python 3.11+ with `from __future__ import annotations`
- Type hints on all function signatures (`def foo(x: str) -> int:`)
- No SQLAlchemy or ORMs — use asyncpg directly with raw SQL
- Async-first: use `async def` and `await` for all I/O operations
- No comments unless the WHY is non-obvious (well-named identifiers explain WHAT)

### Imports

Organized in groups (enforced by `ruff`):

```python
# Standard library
import asyncio
import json
from datetime import UTC, datetime

# Third-party
import asyncpg
import httpx

# Local
from app.core.config import settings
from app.models.document import Radicado
```

### Error Handling

```python
# At system boundaries (API inputs, external calls)
try:
    # external call or user input
except SpecificError as e:
    raise HTTPException(status_code=400, detail=str(e))

# Inside services — let exceptions propagate for tests to catch
```

### Documentation

- Every file has a docstring explaining its purpose (1-3 sentences)
- Every public function has a docstring (1 sentence describing return value)
- No redundant docstrings — the code is the documentation
- Comments explain WHY, not WHAT

Example:

```python
"""
Document service — business logic for radicado lifecycle.

Tracking number assignment is atomic using SELECT FOR UPDATE on
the tracking_sequences table, safe under concurrent requests.
"""

async def create(self, payload: DocumentCreate) -> DocumentResponse:
    """Create and register a new radicado with atomic sequence assignment."""
```

## Pull Request Process

1. Push your branch to the repository
2. Open a PR with a clear title and description:
   - Summary: what changed and why (2-3 bullet points)
   - Testing: how to verify the change
   - Link any related issues
3. Ensure all checks pass (CI, linting, tests)
4. At least one approval from a maintainer before merge
5. Delete the branch after merge

## Debugging

Enable detailed logging:

```bash
# In docker-compose.yml or .env
LOG_LEVEL=DEBUG

# Or in code
import logging
logging.basicConfig(level=logging.DEBUG)
```

For database issues, access the PostgreSQL container directly:

```bash
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db \
  -c "SELECT schema_name FROM information_schema.schemata;"
```

## Out of scope: data migration from legacy Orfeo

**This repository does not accept data-migration tooling for existing Orfeo
installations.** That work is done externally.

Not accepted here: Orfeo-specific ETL, per-client extraction scripts, adapters
for a particular legacy database, or password-rehashing paths for a specific
installation. Every institution configured its Orfeo differently — 14- or
15-digit registration numbers, metadata in dynamic tables, its own password
scheme, different database engines — so this would mean carrying a set of
special cases in the core that help nobody else and age with every client.

What the project does provide, and it is enough: the generic interoperable
importer, `POST /api/v1/import`, whose package format (ZIP + published JSON
Schema + SHA-256 fixity, all-or-nothing validation, original registration number
preserved) is a stable, public contract. **Write the translator outside, against
that contract.**

Do belong here: improvements to the generic importer itself — accepted
registration-number formats, sequence reconciliation after a load, case files or
users in the package, asynchronous jobs. Those affect the input contract and
benefit any source system, not only Orfeo.

See `docs/es/integraciones-externas.md` §7.

## Architecture Decisions

All significant architectural decisions are recorded in `docs/adr/` as Architecture Decision Records (ADRs):

- **ADR-001**: Microservices architecture
- **ADR-002**: Multi-tenancy via schema isolation
- **ADR-003**: asyncpg vs ORM choice

To propose a major change, create a new ADR following the template:

```markdown
# ADR-NNN: Title

**Status**: Pending / Accepted / Rejected

**Context**: Problem statement

**Decision**: What we decided

**Consequences**: Pros and cons
```

## Release Process

Version numbers follow [Semantic Versioning](https://semver.org/):

- **MAJOR**: breaking API changes (e.g., radicado schema)
- **MINOR**: new features backward-compatible (e.g., new endpoint)
- **PATCH**: bug fixes (e.g., off-by-one in sequence)

Release checklist:

- [ ] All commits on `main` with clear messages
- [ ] `CHANGELOG.md` and `docs/es/changelog.md` updated
- [ ] `docs/es/roadmap.md` updated
- [ ] All tests passing
- [ ] Build Docker images and test locally
- [ ] Tag commit: `git tag v0.2.0`
- [ ] Push tags: `git push origin v0.2.0`
- [ ] Publish images to registry

## Questions?

- Open an issue for bugs or feature requests
- Check existing issues/PRs to avoid duplicates
- For architecture discussions, start with an ADR draft
- Contact maintainers: aurigadl@gmail.com

---

**Thank you for contributing to building a free, open-source DMS for Latin America!**
