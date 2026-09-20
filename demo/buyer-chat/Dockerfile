# The demo Buyer Agent. An air-gapped stranger: own folder, own process, own
# deps, own SQLite. Zero imports from either other root, HTTP only.
#
# The build mirrors the repo layout (`/workspace/demo/buyer-chat` beside
# `/workspace/design`) so the same relative token import works here and in local
# dev. Flattening it breaks `@import '../../../design/tokens.css'` in a way that
# only shows up at image build time.
FROM node:26-alpine AS build
WORKDIR /workspace/demo/buyer-chat
# corepack was removed from Node distributions in v25+, so pnpm is installed
# explicitly and pinned to the version that wrote the lockfile.
RUN npm install -g pnpm@11.26.0
COPY demo/buyer-chat/package.json demo/buyer-chat/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
# Assets only, read at build time. The firewall forbids importing anything from
# there, and a test asserts `design/` holds no importable module.
COPY design/ /workspace/design/
COPY demo/buyer-chat/ ./
RUN pnpm exec svelte-kit sync && pnpm build

FROM node:26-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app && mkdir -p /data && chown app:app /data
COPY --from=build /workspace/demo/buyer-chat/build ./build
COPY --from=build /workspace/demo/buyer-chat/node_modules ./node_modules
COPY --from=build /workspace/demo/buyer-chat/package.json ./
USER app
# The agent's key lives here, outside the session DB, so a rebuilt container
# re-registers with the same identity.
VOLUME ["/data"]
EXPOSE 3001
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD wget -qO- http://127.0.0.1:3001/ || exit 1
CMD ["node", "build"]
