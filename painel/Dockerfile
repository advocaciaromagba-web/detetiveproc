# syntax=docker/dockerfile:1
# Painel Next.js em modo standalone (só o necessário para rodar, sem node_modules inteiro).

FROM node:22-alpine AS dependencias
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencias /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS execucao
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3100 \
    HOSTNAME=0.0.0.0
RUN addgroup -S painel && adduser -S painel -G painel
COPY --from=build --chown=painel:painel /app/.next/standalone ./
COPY --from=build --chown=painel:painel /app/.next/static ./.next/static
USER painel
EXPOSE 3100
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3100/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
