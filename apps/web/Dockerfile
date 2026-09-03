# Next.js frontend, built as a standalone server.
FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
# Cap the heap: this builds on a 1 vCPU / 2 GiB host that is already running
# other stacks, and an uncapped Next build is the most likely thing to be
# OOM-killed there. Override at build time with --build-arg if the host is
# roomier.
ARG NODE_BUILD_MEMORY=640
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_OPTIONS=--max-old-space-size=${NODE_BUILD_MEMORY}
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/ ./
# next.config.ts sets output:"standalone", so the build emits a self-contained
# server with only the modules it actually uses.
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 10001 -S nodejs && adduser -S -u 10001 -G nodejs nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
