# One command to a verified receipt. Anything here that needs a hand-edited file
# before it runs is a bug in the install gate, not a documentation problem.

.DEFAULT_GOAL := help
.PHONY: help up down seed demo test check guardrails logs fmt

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the whole stack, then seed it
	docker compose up -d --build
	@echo "Waiting for the shop to answer..."
	@# `/healthz`, not `/`. The home page needs a seeded catalogue, and the seed
	@# runs after this wait — waiting for `/` is waiting for the thing this step
	@# is about to do.
	@for i in $$(seq 1 60); do \
		curl -fsS http://127.0.0.1:3000/healthz >/dev/null 2>&1 && break; \
		sleep 2; \
	done
	@$(MAKE) --no-print-directory seed
	@echo "Checking the shop renders..."
	@curl -fsS -H 'Host: spoiledduckie.localhost' http://127.0.0.1/ >/dev/null \
		|| { echo "The shop did not render after seeding."; docker compose logs --no-color store | tail -20; exit 1; }
	@echo ""
	@echo "  Shop     http://spoiledduckie.localhost"
	@echo "  Console  http://spoiledduckie.localhost/agentic"
	@echo "  Chat     http://chat.localhost"
	@echo "  Card     http://spoiledduckie.localhost/.well-known/agent-commerce.json"
	@echo ""
	@echo "  *.localhost resolves to loopback in Chrome and Firefox. A container"
	@echo "  does not resolve it — that is §10.1, and it is the single most"
	@echo "  likely way to lose an afternoon."

seed: ## Seed the merchant catalogue (12 groups, 15 items)
	docker compose exec -T store node --experimental-strip-types scripts/seed.ts

down: ## Stop everything and remove the volumes
	docker compose down -v

logs: ## Follow the logs
	docker compose logs -f

test: ## Every suite: sidecar, merchant site, chat
	uv run pytest -q
	cd demo/merchant-site && pnpm vitest run
	cd demo/buyer-chat && pnpm vitest run

guardrails: ## The five checks that fail the build on drift
	uv run scripts/lint_firewall.py
	uv run scripts/registry_diff.py --check
	uv run scripts/registry_diff.py --prose
	uv run scripts/lint_money.py
	uv run scripts/lint_time.py
	@# The vectors a second Merchant-side implementation is held to. Drift here
	@# is §16.11 or the HMAC preimage moving under an implementation that cannot
	@# see it change.
	uv run scripts/make_trait_vectors.py --check

check: guardrails ## Guardrails, lint, types, and every suite
	uv run ruff check src scripts tests
	uv run ruff format --check src scripts tests
	uv run mypy src scripts
	cd demo/merchant-site && pnpm exec svelte-check --tsconfig ./tsconfig.json
	cd demo/buyer-chat && pnpm exec svelte-check --tsconfig ./tsconfig.json
	@$(MAKE) --no-print-directory test

woo: ## Check the WooCommerce plugin against the sidecar's own vectors
	@# No PHP on the host is the normal case, so the runtime comes from a
	@# container. Neither runner needs WordPress: the two things checked here
	@# are integers and bytes.
	docker run --rm -v "$(PWD):/src:ro" php:8.2-cli \
		bash -c 'for f in $$(find /src/integrations/woocommerce -name "*.php"); do php -l "$$f" >/dev/null || exit 1; done'
	docker run --rm -v "$(PWD):/src:ro" php:8.2-cli php /src/integrations/woocommerce/tests/run-vectors.php
	docker run --rm -v "$(PWD):/src:ro" php:8.2-cli php /src/integrations/woocommerce/tests/run-signing.php

fmt: ## Format the Python
	uv run ruff check --fix src scripts tests
	uv run ruff format src scripts tests

demo: ## Conformance AND a whole purchase, against the running stack
	@# A purchase is a few dozen agent calls, so two runs inside a minute meet
	@# the shop's own per-IP limit. That is the limiter working — the suite
	@# skips rather than failing, and a minute later it runs clean.
	OPENSTORE_LIVE_TRAIT_URL=http://127.0.0.1:3000 uv run pytest tests/test_trait_live.py -q
	@# The nine doors passing says the contract holds. This says the product
	@# works: search to signed receipt over HTTP, through the real Gate, Ledger
	@# and Provider. Every phase was green while this could not have passed.
	OPENSTORE_LIVE_ORIGIN=http://127.0.0.1 uv run pytest tests/test_purchase_live.py -q
