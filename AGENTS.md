# AGENTS.md

## Purpose
This repository contains a modern FastAPI application.

Agents working in this repo MUST follow the conventions and constraints below.
When in doubt: prefer simplicity, type safety, and explicitness.

---

## Tech Stack

- Python >= 3.11
- FastAPI (async-first)
- Pydantic v2
- SQLAlchemy 2.0 (async)
- uv (package manager)
- pytest (+ pytest-asyncio)

---

## Project Structure

Use a layered architecture:

- app/
  - main.py              # FastAPI entrypoint
  - api/                 # routers (HTTP layer)
  - services/            # business logic
  - repositories/        # DB access
  - models/              # ORM models
  - schemas/             # Pydantic models
  - core/                # config, settings, security
  - db/                  # database setup

Rules:
- Routers MUST NOT contain business logic
- Services MUST be pure (no HTTP concerns)
- Repositories MUST handle persistence only

---

## Coding Style

- Follow PEP8
- Use Black-compatible formatting (line length 88)
- Use Ruff for linting
- Use type hints everywhere (mypy-compatible)
- Use `str | None` instead of Optional[str]

Docstrings:
- Use Google-style docstrings
- Document all public functions

---

## FastAPI Conventions

- Use dependency injection via `Depends`
- All endpoints MUST be async
- Use response models (`response_model=...`)
- Validate all input with Pydantic schemas
- Never return raw ORM models

Example pattern:

Router → Service → Repository

---

## Configuration

- Use `pydantic-settings` for configuration
- No hardcoded secrets
- All environment variables MUST be documented

---

## Database

- Use SQLAlchemy 2.0 async API
- Use `async_sessionmaker`
- No blocking DB calls

Rules:
- No direct DB access outside repositories
- Always use transactions where needed

---

## Error Handling

- Use HTTPException for API errors
- Do not leak internal errors
- Log errors before raising

---

## Logging

- Use structured logging (no print statements)
- Log at appropriate levels:
  - INFO: normal operations
  - WARNING: recoverable issues
  - ERROR: failures

---

## Testing

- Use pytest
- Use pytest-asyncio for async tests
- Write tests for:
  - services (unit tests)
  - API endpoints (integration tests)

Rules:
- Minimum: test happy path + one failure case
- No network calls in unit tests

---

## Dependencies (uv)

- Add packages using:

  uv add <package>

- Dev dependencies:

  uv add --dev <package>

- NEVER edit pyproject.toml manually unless necessary

---

## Security Rules

- NEVER:
  - execute arbitrary shell commands
  - expose secrets in code
  - write to production systems without explicit instruction

- ALWAYS:
  - validate input
  - sanitize external data
  - use parameterized queries (SQLAlchemy handles this)

---

## Performance Guidelines

- Prefer async everywhere
- Avoid blocking I/O
- Use pagination for large queries
- Avoid N+1 queries (use joins/selectinload)

---

## API Design

- Follow REST conventions
- Use plural resource names (`/users`)
- Use proper HTTP methods:
  - GET, POST, PUT/PATCH, DELETE

---

## Migrations

- Use Alembic
- Do not modify DB schema manually
- Always generate migrations

---

## What NOT to do

- Do NOT:
  - mix layers (e.g. DB logic in routers)
  - create overly complex abstractions
  - introduce new frameworks without justification
  - ignore type errors

---

## Agent Workflow Expectations

When implementing features:

1. Understand existing structure
2. Follow existing patterns
3. Add/update:
   - schemas
   - service logic
   - repository methods
   - router endpoints
4. Add tests
5. Ensure type correctness

---

## Definition of Done

A change is complete when:

- Code compiles and type-checks
- Tests pass
- API schema is valid (OpenAPI)
- No lint errors
- Feature follows architecture rules

---

## Guiding Principles

- Clarity over cleverness
- Explicit over implicit
- Small, composable functions
- Production safety first