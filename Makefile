# One command to a verified receipt. Anything here that needs a hand-edited file
# before it runs is a bug in the install gate, not a documentation problem.

.DEFAULT_GOAL := help
.PHONY: help up down seed demo test check guardrails logs fmt

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the whole stack, then seed it
	docker compose up -d --build
	@echo "Waiting for the shop..."
	@for i in $$(seq 1 60); do \
		curl -fsS -H 'Host: spoiledduckie.localhost' http://127.0.0.1/ >/dev/null 2>&1 && break; \
		sleep 2; \
	done
	@$(MAKE) --no-print-directory seed
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

guardrails: ## The four checks that fail the build on drift
	uv run scripts/lint_firewall.py
	uv run scripts/registry_diff.py --check
	uv run scripts/registry_diff.py --prose
	uv run scripts/lint_money.py
	uv run scripts/lint_time.py

check: guardrails ## Guardrails, lint, types, and every suite
	uv run ruff check src scripts tests
	uv run ruff format --check src scripts tests
	uv run mypy src scripts
	cd demo/merchant-site && pnpm exec svelte-check --tsconfig ./tsconfig.json
	cd demo/buyer-chat && pnpm exec svelte-check --tsconfig ./tsconfig.json
	@$(MAKE) --no-print-directory test

fmt: ## Format the Python
	uv run ruff check --fix src scripts tests
	uv run ruff format src scripts tests

demo: ## The conformance suite against the running store
	OPENSTORE_LIVE_TRAIT_URL=http://127.0.0.1:3000 uv run pytest tests/test_trait_live.py -q
