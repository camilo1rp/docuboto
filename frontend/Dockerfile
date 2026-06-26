# Frontend OrpycaMCP — build de producción (SvelteKit + adapter-node SSR).
# Patrón multi-stage alineado con sgdINTI. El build ocurre SIEMPRE en Docker
# (regla Docker-only del proyecto); no se instalan dependencias en el host.

# --- Build stage ---
FROM node:18-alpine AS builder
WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

# --- Production stage ---
FROM node:18-alpine AS runner
WORKDIR /app

COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/build ./build

ENV NODE_ENV=production
ENV PORT=3000

# --- URLs públicas (navegador del usuario) ---
# Sobreescribir en despliegue con la URL real accesible por los usuarios.
ENV PUBLIC_API_URL=http://localhost:19080
ENV PUBLIC_KEYCLOAK_URL=http://localhost:19180

# --- URLs internas (SSR dentro de la red Docker) ---
# El servidor SSR usa estas URLs para hacer fetch al gateway y a Keycloak.
# Con $env/dynamic/private se leen en runtime, no se hornean en el build.
ENV INTERNAL_API_URL=http://api-gateway:8080
ENV INTERNAL_KEYCLOAK_URL=http://keycloak:8080

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost:3000/ || exit 1

# No ejecutar como root para evitar artefactos root en volúmenes y reducir superficie de ataque.
USER node

CMD ["node", "build"]
