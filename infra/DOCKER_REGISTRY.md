# Registro de imágenes (GitHub Container Registry)

Docuboto publica sus imágenes en **GHCR** desde `.github/workflows/release.yml`, solo cuando se
empuja un tag de versión `X.Y.Z` (ADR-053 §3). No se publican imágenes por commit.

## Qué publica un lanzamiento

Por cada imagen de `scripts/release.py plan` (la lista sale de `infra/docker-compose.build.yml`):

```
ghcr.io/<propietario>/docuboto/<servicio>:<versión>
```

con `ORPYCA_VERSION=<versión>` dentro (la expone `/health`). Cada imagen se escanea con Trivy
**antes** de subirse: una CRÍTICA con corrección disponible detiene el lanzamiento.

Con los digests, `scripts/release.py manifest` genera `release-<versión>.yml`: el manifiesto de
producción con cada imagen escrita como `…:<versión>@sha256:…`. Se adjunta al Release de GitHub y
es lo que se despliega. Un tag de registro se puede mover; un digest no.

## Autenticación

- **En el workflow**: `GITHUB_TOKEN` con `packages: write`.
- **En un servidor**: las imágenes nacen privadas. O se hacen públicas (el código es AGPL), o
  `docker login ghcr.io` con un token de solo `read:packages`.

## Sin registro: construir en el propio servidor

```bash
git checkout 1.0.0
export ORPYCA_REGISTRY=local ORPYCA_VERSION=1.0.0
docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.build.yml \
  build --build-arg ORPYCA_VERSION=1.0.0
```

## Arquitectura

`linux/amd64`. ARM64 no está soportado hoy (habría que añadir `buildx` + QEMU al trabajo `images`).

## Pendiente

- [ ] Firma de imágenes (Cosign)
- [ ] Multi-arquitectura (ARM64)
