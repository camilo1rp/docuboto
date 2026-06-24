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
# URL del api-gateway; se inyecta en despliegue.
ENV PUBLIC_API_URL=http://api-gateway:8080

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost:3000/ || exit 1

CMD ["node", "build"]
