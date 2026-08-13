# CSP-451 Final Project — ShopStream Supplier Sync

> **Open `FinalProject_Assignment_Brief.docx` first.** This README is a quick-reference companion only.

## How this builds on Milestones 1 & 2

This is the final step of one continuous project. In **Milestone 1** you built ShopStream's hardened three-tier Azure network (Web/App/DB subnets + NSG tier isolation). In **Milestone 2** you added Suricata IDS and VNet Flow Log monitoring to it. In **Milestone 3** you extend that same environment with an event-driven supplier-coordination feature: when stock runs low, ShopStream publishes an event to an Azure Storage Queue, an Azure Function forwards an order to a Supplier API, and a correlation ID lets you trace one sale to its supplier confirmation in Log Analytics.

**Prerequisite:** your `ShopStream-VNet` from Milestone 1 must still exist — `setup-azure.sh` adds a new `Supplier-Subnet` (10.0.5.0/24) to it and deploys into it. If you have torn it down, rebuild the ShopStream baseline first with the Milestone 2 starter (`bash setup_infrastructure.sh`). Task 4 then reuses your Milestone 2 VNet Flow Logs on `ShopStream-VNet`.

## When to start

- **Weeks 1–12:** Do not start the final project. Focus on each week's CheckPoint and (in Week 9) your Demo Presentation.
- **Week 14 (Final Assessment Period — see Blackboard for dates):** Start now.
- **Due:** the deadline posted on Blackboard (within the Final Assessment Period — see Blackboard for current-term dates).

## Reading order

1. `FinalProject_Assignment_Brief.docx` — required reading. Defines scope, parts, deliverables, rubric.
2. `FinalProject_Notes.docx` — background concepts (event-driven architecture, queues, Azure Functions, correlation IDs, App Insights).
3. `FinalProject_Walkthrough.docx` — step-by-step build / deploy / test instructions.

## What's in the codebase

```
backend/         FastAPI service that emits stock events
supplier-api/    FastAPI microservice that simulates the supplier
azure-function/  Azure Function v4 (Python) that consumes the queue and forwards to supplier-api
scripts/         setup-venv.sh, setup-azure.sh, deploy-azure.sh, test-local.sh
docker-compose.yml — runs backend + supplier-api + Azurite (local queue emulator) + Portainer locally
.env.example     copy to .env and fill in values; never commit .env
```

## Quick start (Week 14 — Final Project)

```bash
# 1. Set up Python virtualenv
./scripts/setup-venv.sh

# 2. Provision Azure resources (uses your 2026S subscription)
./scripts/setup-azure.sh

# 3. Run the full stack locally for development
docker compose up --build

# 4. Test the end-to-end flow
./scripts/test-local.sh

# 5. Deploy to Azure when ready
./scripts/deploy-azure.sh
```

## Submission

See `FinalProject_Assignment_Brief.docx` for the full deliverables checklist. Quick version: PDF/DOCX report with labelled screenshots and 1–2 sentence illustrations of correlated logs across all three services. **Do NOT submit a GitHub repository URL** — submission is graded entirely from the screenshots in your report.
