.DEFAULT_GOAL := help
SHELL := /bin/sh

# SOLUTION=1 swaps your checkout/ and support/ for the ones in solutions/. Remembered in .solution
# until `make down`.
SOLUTION_ON := $(or $(SOLUTION),$(shell cat .solution 2>/dev/null),0)
COMPOSE := docker compose $(if $(filter 1,$(SOLUTION_ON)),-f docker-compose.yml -f docker-compose.solutions.yml)
CODE    := $(if $(filter 1,$(SOLUTION_ON)),solutions/)
# A worker starts once its worker.py exists: checkout's from Part 1 step 3, support's from Part 2 step 9.
WORKERS  = $(if $(wildcard $(CODE)checkout/worker.py),worker) $(if $(wildcard $(CODE)support/worker.py),support-worker)
CLI     := $(COMPOSE) --progress quiet run --rm --no-deps cli
UI_URL  := http://localhost:8233

# Every buy writes its order ID to .last-order; other commands read it. Override with ID=...
NEW_ID   := $(or $(ID),order-$(shell od -An -N2 -tx1 /dev/urandom | tr -d ' \n'))
ORDER_ID  = $(or $(ID),$(shell cat .last-order 2>/dev/null))
CUSTOMER ?= alice
SKU      ?= TSHIRT-M

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
		/^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@test "$(SOLUTION_ON)" != 1 || printf "\nRunning solutions/ (make up-temporal SOLUTION=0 to go back to yours)\n"

##@ Start and stop

.PHONY: preflight setup check-key up up-temporal down reset ui psql
PORTS := 8000 8001 8002 8003 5432 7233 8233
preflight: ## Before the workshop: check this machine has what the workshop needs
	@command -v docker >/dev/null || { echo "✗ docker not found: install Docker Desktop, or Docker Engine with the Compose plugin"; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "✗ Docker is installed but not running: start Docker Desktop (or the docker service)"; exit 1; }
	@echo "✓ Docker $$(docker version --format '{{.Server.Version}}') is running"
	@v=$$(docker compose version --short 2>/dev/null | sed 's/^v//') || { echo "✗ 'docker compose' not found: install the Compose v2 plugin"; exit 1; }; \
		echo "$$v" | awk -F. '{ exit !($$1 > 2 || ($$1 == 2 && ($$2 > 24 || ($$2 == 24 && $$3 + 0 >= 4)))) }' \
		&& echo "✓ Docker Compose $$v" \
		|| { echo "✗ Docker Compose $$v is too old: 2.24.4 or later is needed"; exit 1; }
	@mem=$$(docker info --format '{{.MemTotal}}'); \
		if [ "$$mem" -ge 3800000000 ]; then echo "✓ Docker has $$((mem / 1000000000)) GB of memory"; \
		else echo "! Docker has $$((mem / 1000000)) MB of memory: give it 4 GB or more (Docker Desktop → Settings → Resources)"; fi
	@if [ -n "$$($(COMPOSE) --profile temporal ps -q 2>/dev/null)" ]; then echo "✓ Ports: the workshop is already running"; \
		elif ! command -v nc >/dev/null; then echo "! Ports: can't check (no 'nc'); make sure $(PORTS) are free"; \
		else busy=""; for p in $(PORTS); do nc -z 127.0.0.1 $$p 2>/dev/null && busy="$$busy $$p"; done; \
		if [ -z "$$busy" ]; then echo "✓ Ports $(PORTS) are free"; \
		else echo "✗ Ports in use:$$busy. Stop whatever uses them, or set other ports in .env"; exit 1; fi; fi
	@echo "Ready. Next: make setup"

setup: ## Before the workshop: download and build everything (needs a good connection)
	@$(COMPOSE) --profile temporal --profile tools pull --ignore-buildable --quiet
	@$(COMPOSE) --profile temporal --profile tools build --quiet
	@echo "Ready. Start with: make up"

check-key: ## Part 2: check your ANTHROPIC_API_KEY in .env works (one tiny call to Claude)
	@$(COMPOSE) --progress quiet run --rm --no-deps -v ./scripts/check_key.py:/check_key.py --entrypoint python support /check_key.py

up: ## Start the shop: checkout, inventory, payment provider, database, support
	@echo $(SOLUTION_ON) > .solution
	@$(COMPOSE) up -d --build --wait --remove-orphans postgres inventory payment_provider checkout support
	@echo "Shop is up. Checkout API: http://localhost:8000/docs"

up-temporal: ## Also start the Temporal server and your workers (from step 3 on)
	@echo $(SOLUTION_ON) > .solution
	@$(COMPOSE) --profile temporal up -d --build --wait --remove-orphans postgres inventory payment_provider checkout support temporal $(WORKERS)
	@echo "Temporal UI: $(UI_URL)"

down: ## Stop everything. The database and the Temporal history are wiped, and SOLUTION goes back to 0
	@$(COMPOSE) --profile temporal --profile tools down --remove-orphans --volumes
	@rm -f .solution

reset: down ## Start again from a clean slate
	@rm -f .last-order
	@$(MAKE) --no-print-directory up

ui: ## Open the Temporal UI
	@echo "$(UI_URL)"
	@(command -v open >/dev/null && open "$(UI_URL)") || (command -v xdg-open >/dev/null && xdg-open "$(UI_URL)") || true

psql: ## Open a SQL prompt on the shop database
	@$(COMPOSE) exec postgres psql -U shop -d shop

##@ Shop

.PHONY: buy status board stock ledger chat
buy: ## Buy a T-shirt and wait for the answer (CUSTOMER=bob has a card that's declined; SKU=HOODIE-L)
	@echo "$(NEW_ID)" > .last-order
	@$(CLI) buy --id $(NEW_ID) --customer $(CUSTOMER) --sku $(SKU)

status: ## What checkout, inventory and payments know about the last order
	@$(CLI) status $(ORDER_ID)

board: ## Live board: services, faults, the last order (leave it running in a second pane)
	@$(CLI) board

stock: ## Stock, and the latest reservations
	@$(CLI) stock

ledger: ## Everything the payment provider recorded
	@$(CLI) ledger

chat: ## Part 2: chat with support about the last order (or ID=old-alice-1)
	@test -n "$(ORDER_ID)" || { echo "No order yet: make buy first, or pass ID=old-alice-1"; exit 1; }
	@$(CLI) chat $(ORDER_ID)

##@ Break the external services (flags: deterministic, instant)

.PHONY: payments-down payments-up payments-slow payments-flaky inventory-down inventory-up inventory-refuse faults-off
payments-down: ## The payment provider answers 503 to everything
	@$(CLI) fault payments --down

payments-up: ## The payment provider is back to normal
	@$(CLI) fault payments

payments-slow: ## Every payment call takes 5s: longer than checkout waits
	@$(CLI) fault payments --latency-s 5

payments-flaky: ## The next 3 payment calls fail with a 503
	@$(CLI) fault payments --fail-next 3

inventory-down: ## The warehouse answers 503 to everything
	@$(CLI) fault inventory --down

inventory-up: ## The warehouse is back to normal
	@$(CLI) fault inventory

inventory-refuse: ## The warehouse refuses to ship anything
	@$(CLI) fault inventory --refuse-shipping

faults-off: payments-up inventory-up ## Everything back to normal

##@ Crash a process (SIGKILL, like a machine dying)

.PHONY: kill-checkout start-checkout kill-worker start-worker restart-worker
.PHONY: kill-support start-support kill-support-worker start-support-worker
kill-checkout: ## Kill the checkout API
	@$(COMPOSE) kill -s SIGKILL checkout >/dev/null 2>&1; echo "checkout killed. make start-checkout"

start-checkout: ## Start the checkout API again
	@$(COMPOSE) up -d --no-deps --wait checkout >/dev/null 2>&1; echo "checkout is back"

kill-worker: ## Kill your checkout worker
	@$(COMPOSE) --profile temporal kill -s SIGKILL worker >/dev/null 2>&1; echo "worker killed. make start-worker"

start-worker: ## Start your checkout worker again
	@$(COMPOSE) --profile temporal up -d --no-deps worker >/dev/null 2>&1; echo "worker is back"

restart-worker: ## Restart your workers: do this after you change workflow, worker or activity code
	@test -n "$(strip $(WORKERS))" || { echo "No worker.py yet"; exit 1; }
	@$(COMPOSE) --profile temporal up -d --no-deps --force-recreate $(WORKERS) >/dev/null 2>&1; echo "restarted:$(WORKERS)"

kill-support: ## Part 2: kill the support API
	@$(COMPOSE) kill -s SIGKILL support >/dev/null 2>&1; echo "support killed. make start-support"

start-support: ## Part 2: start the support API again
	@$(COMPOSE) up -d --no-deps --wait support >/dev/null 2>&1; echo "support is back"

kill-support-worker: ## Part 2: kill your support worker
	@$(COMPOSE) --profile temporal kill -s SIGKILL support-worker >/dev/null 2>&1; echo "support worker killed. make start-support-worker"

start-support-worker: ## Part 2: start your support worker again
	@$(COMPOSE) --profile temporal up -d --no-deps support-worker >/dev/null 2>&1; echo "support worker is back"

##@ Workshop

.PHONY: check solution try-workflow test logs logs-checkout logs-worker logs-support
# Part 1 (steps 1-6) checks checkout/; Part 2 (steps 7-10) checks support/. Never the solution overlay.
CHECKS = docker compose --progress quiet run --rm --no-deps -v ./scripts/checks/$(1):/checks \
	--entrypoint python $(1) -m pytest /checks -q -p no:cacheprovider
check: ## Check your code for steps 1..STEP (STEP=2), or Part 2's 7..STEP (STEP=8)
	@test -n "$(STEP)" || { echo "Which step? make check STEP=2"; exit 1; }
	@if [ "$(STEP)" -le 6 ]; then CHECKOUT_CODE=./checkout $(call CHECKS,checkout) --step $(STEP); \
		else SUPPORT_CODE=./support $(call CHECKS,support) --step $(STEP); fi

SERVICE ?= checkout
solution: ## Show how your code differs from the solution (SERVICE=support, FILE=workflows.py)
	@diff -u -x __pycache__ $(if $(FILE),$(SERVICE)/$(FILE) solutions/$(SERVICE)/$(FILE),-r $(SERVICE) solutions/$(SERVICE)); true

try-workflow: ## Step 3: run a CheckoutWorkflow from the Temporal CLI, without the API
	@echo "$(NEW_ID)" > .last-order
	@$(COMPOSE) exec temporal temporal workflow execute --type CheckoutWorkflow --task-queue checkout \
		--workflow-id $(NEW_ID) --input '{"order_id": "$(NEW_ID)", "customer_id": "$(CUSTOMER)", "sku": "TSHIRT-M", "quantity": 1, "amount_cents": 49900}'

test: ## Run every check against the solutions (should always pass)
	@CHECKOUT_CODE=./solutions/checkout $(call CHECKS,checkout)
	@SUPPORT_CODE=./solutions/support $(call CHECKS,support)

logs: ## Follow every service's logs
	@$(COMPOSE) --profile temporal logs -f --tail 20

logs-checkout: ## Follow the checkout API's logs
	@$(COMPOSE) logs -f --tail 20 checkout

logs-worker: ## Follow your workers' logs: every attempt and retry
	@$(COMPOSE) --profile temporal logs -f --tail 20 $(WORKERS)

logs-support: ## Part 2: follow the support API's logs
	@$(COMPOSE) logs -f --tail 20 support
