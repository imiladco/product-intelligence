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

# --- Release provenance ------------------------------------------------------
# Which commit built this image. A web image from another commit is exactly as
# wrong as an API image from another commit, so the deploy path verifies this
# label on both before running a release step.
#
# Declared in the runner stage: an ARG or LABEL in a builder stage never
# reaches the published image. Last in the file so it cannot invalidate the
# dependency or build layers.
#
# There is deliberately no migration-compatibility label here: only the API has
# migrations, and a second answer to a question this image cannot have would be
# one more thing to keep in sync.
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA
