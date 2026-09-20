# SpoiledDuckie — the demo Merchant. Its own container, its own database, and
# no shared anything with the sidecar (ADR-0007).
#
# The build mirrors the repo layout (`/workspace/demo/merchant-site` beside
# `/workspace/design`) so the same relative token import works here and in local
# dev. Flattening it breaks `@import '../../../design/tokens.css'` in a way that
# only shows up at image build time.
FROM node:26-alpine AS build
WORKDIR /workspace/demo/merchant-site
# corepack was removed from Node distributions in v25+, so pnpm is installed
# explicitly and pinned to the version that wrote the lockfile.
RUN npm install -g pnpm@11.26.0
COPY demo/merchant-site/package.json demo/merchant-site/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
# Assets only, read at build time. The firewall forbids importing anything from
# there, and a test asserts `design/` holds no importable module.
COPY design/ /workspace/design/
COPY demo/merchant-site/ ./
RUN pnpm exec svelte-kit sync && pnpm build

FROM node:26-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /workspace/demo/merchant-site/build ./build
COPY --from=build /workspace/demo/merchant-site/node_modules ./node_modules
COPY --from=build /workspace/demo/merchant-site/package.json ./
# The seed script runs in this image and imports from src/lib, so the source
# comes along. It is small, and shipping the schema without the code that
# applies it is how `docker compose exec store seed` fails at 2am.
COPY --from=build /workspace/demo/merchant-site/src/lib ./src/lib
COPY --from=build /workspace/demo/merchant-site/scripts ./scripts
USER app
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD wget -qO- http://127.0.0.1:3000/healthz || exit 1
CMD ["node", "build"]
