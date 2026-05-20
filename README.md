# Cherwell → ServiceNow Migration Tool

An agentic data-migration application that moves ITSM records from
**Cherwell** to **ServiceNow** with schema mapping, dry-run validation,
human-in-the-loop approval, and post-migration validation.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   MigrationOrchestrator                     │
│                   (State Machine Engine)                    │
└──────────┬──────────────────────────────────────────────────┘
           │ dispatches to
    ┌──────▼──────┐
    │  Stage Loop │
    └──────┬──────┘
           │
    ┌──────▼─────────────┐   ┌──────────────────────┐
    │ SchemaMappingAgent │──▶│ CherwellConnector     │
    │  (fetches & maps   │   │ ServiceNowConnector   │
    │   field schemas)   │   └──────────────────────┘
    └──────┬─────────────┘
           │
    ┌──────▼─────────────┐
    │   DryRunAgent      │  Transforms all records, validates against
    │ (validate only,    │  target schema, checks for duplicates.
    │  no writes)        │  Produces a DryRunResult report.
    └──────┬─────────────┘
           │
    ┌──────▼─────────────┐
    │   ApprovalAgent    │  Presents summary to operator.
    │ (human-in-loop)    │  Requires explicit approve/reject.
    └──────┬─────────────┘
           │ approved
    ┌──────▼─────────────┐
    │  MigrationAgent    │  Batch-creates records in ServiceNow
    │  (batch migrate,   │  with exponential-backoff retries.
    │   retry, rollback) │
    └──────┬─────────────┘
           │
    ┌──────▼─────────────┐
    │ ValidationAgent    │  Verifies every migrated record is
    │ (post-migration)   │  retrievable and fields match source.
    └────────────────────┘
```

### Pipeline Stages

| Stage | Description |
|---|---|
| `INITIALIZE` | Bootstraps state |
| `FETCH_SCHEMA` | Retrieves Cherwell + ServiceNow field schemas |
| `MAP_SCHEMA` | Generates field-level mapping with value transforms |
| `FETCH_RECORDS` | Pulls all source records (paginated) |
| `DRY_RUN` | Transforms & validates without writing |
| `AWAIT_APPROVAL` | Waits for human operator approval |
| `MIGRATE` | Creates records in ServiceNow in batches |
| `VALIDATE` | Confirms migrated records match source |
| `COMPLETE` / `FAILED` | Terminal states |

---

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### 2. Configure

```bash
copy .env.example .env
# Edit .env with your credentials
```

Key settings:

| Variable | Default | Description |
|---|---|---|
| `MIGRATION_MOCK_MODE` | `true` | Use synthetic data (no real API calls) |
| `MIGRATION_AUTO_APPROVE` | `false` | Skip human approval prompt |
| `MIGRATION_BATCH_SIZE` | `50` | Records per batch |
| `CHERWELL_BASE_URL` | – | Cherwell server URL |
| `SERVICENOW_INSTANCE_URL` | – | ServiceNow instance URL |

### 3. Run

```bash
# Full pipeline (interactive approval)
python main.py run

# Dry run only
python main.py dry-run

# Check status of last run
python main.py status

# Resume a failed run
python main.py run --resume-id <migration-id>
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```
cherwell-to-servicenow-migration/
├── main.py                       # CLI entry point (Click)
├── requirements.txt
├── .env.example                  # Configuration template
│
├── config/
│   └── settings.py               # Pydantic-Settings configuration
│
├── models/
│   └── data_models.py            # All Pydantic data models
│
├── connectors/
│   ├── cherwell_connector.py     # Cherwell REST API client
│   └── servicenow_connector.py   # ServiceNow Table API client
│
├── agents/
│   ├── schema_mapper_agent.py    # Schema analysis & mapping
│   ├── dry_run_agent.py          # Simulation & validation
│   ├── approval_agent.py         # Human-in-the-loop approval
│   ├── migration_agent.py        # Batch migration execution
│   └── validation_agent.py      # Post-migration verification
│
├── orchestrator/
│   └── orchestrator.py           # State-machine pipeline manager
│
├── utils/
│   ├── logger.py                 # Rich-formatted logging
│   └── report_generator.py       # Console & JSON reports
│
└── tests/
    ├── conftest.py
    ├── test_agents.py
    ├── test_connectors.py
    └── test_orchestrator.py
```

---

## Security Notes

- Credentials are loaded exclusively from environment variables / `.env` file
- `.env` is git-ignored — never commit credentials
- SSL verification is enabled by default (`CHERWELL_VERIFY_SSL=true`)
- API tokens are held in memory only, never logged
- All input is validated with Pydantic before use
