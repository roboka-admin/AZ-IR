# Web image. Build context is the repository root:
#   docker build -f frontend/Dockerfile .
FROM node:22-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 \
    AZIR_STANDALONE=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
# The build talks to the API for the server-rendered pages; point it at a reachable backend.
ARG AZIR_BACKEND_URL=http://127.0.0.1:8000
ENV AZIR_BACKEND_URL=$AZIR_BACKEND_URL
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nodejs /app/public ./public
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
