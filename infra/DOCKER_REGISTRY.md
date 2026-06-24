# Docker Registry Setup

OrpycaMCP uses **GitHub Container Registry (GHCR)** for publishing Docker images.

## Automated Builds

Images are built and pushed automatically by GitHub Actions:

- **On every push to `main`**: Images tagged with `main` + `latest`
- **On version tags** (e.g., `v1.0.0`): Images tagged with version + major.minor
- **On pull requests**: Images are built but NOT pushed (validation only)

## Available Images

After merge to `main`, images are available at:

```bash
ghcr.io/orpyca/orpyca-mcp/api-gateway:latest
ghcr.io/orpyca/orpyca-mcp/auth-service:latest
ghcr.io/orpyca/orpyca-mcp/tenant-service:latest
ghcr.io/orpyca/orpyca-mcp/document-service:latest
ghcr.io/orpyca/orpyca-mcp/storage-service:latest
ghcr.io/orpyca/orpyca-mcp/workflow-service:latest
ghcr.io/orpyca/orpyca-mcp/archive-service:latest
ghcr.io/orpyca/orpyca-mcp/notification-service:latest
```

## Using Images in docker-compose

For **development** (pull from local Dockerfile):
```bash
docker compose up
```

For **production** (pull from GHCR):
```bash
# Update docker-compose.prod.yml to use images
docker compose -f docker-compose.prod.yml up -d
```

Example in docker-compose.prod.yml:

```yaml
api-gateway:
  image: ghcr.io/orpyca/orpyca-mcp/api-gateway:latest
  # ... rest of config
```

## Pulling Images Manually

```bash
# Login to GitHub Container Registry
docker login ghcr.io

# Pull an image
docker pull ghcr.io/orpyca/orpyca-mcp/document-service:v1.0.0

# Run it
docker run -it ghcr.io/orpyca/orpyca-mcp/document-service:latest
```

## Authentication

### In GitHub Actions (automatic)
Images are pushed using `GITHUB_TOKEN` (automatically available in workflows).

### On your local machine
1. Generate a GitHub Personal Access Token (PAT) with `read:packages` scope
2. Login: `docker login ghcr.io -u {username} -p {token}`

### On production servers
1. Create a service account or use repository deployment tokens
2. Create a pull-only token with `read:packages` scope
3. Store in `.docker/config.json` or use `docker login`

## Image Architecture

Images are built for **`linux/amd64`** (Intel/AMD 64-bit).

To add ARM64 support (e.g., Apple Silicon or ARM servers):

1. Enable Docker buildx on your runner or use official runners
2. Update `.github/workflows/docker-build.yml`:

```yaml
- name: Set up QEMU
  uses: docker/setup-qemu-action@v3

- name: Build and push
  uses: docker/build-push-action@v5
  with:
    platforms: linux/amd64,linux/arm64
```

## Tagging Strategy

| Event | Tag | Example |
|---|---|---|
| Push to main | `latest`, `main` | `api-gateway:latest`, `api-gateway:main` |
| Version tag | `v{version}`, `v{major}.{minor}` | `api-gateway:v1.0.0`, `api-gateway:v1.0` |
| Commit SHA | `{branch}-{sha}` | `api-gateway:main-abc1234` |

## Troubleshooting

### "Error: no such image" when running docker-compose
→ Images are published only after merging to `main`. For local development, use `docker compose up` (builds from Dockerfile).

### "Permission denied" on pull
→ Check authentication: `docker login ghcr.io` with valid PAT.

### Image size too large
→ Check Dockerfile: ensure `pip cache --purge`, minimal base images (python:3.11-slim), and .dockerignore files.

## Future Enhancements

- [ ] Image scanning for vulnerabilities (Trivy)
- [ ] Image signing with Cosign
- [ ] Multi-arch builds (ARM64)
- [ ] Helm charts for Kubernetes deployment
- [ ] Private registry mirror (for air-gapped environments)
