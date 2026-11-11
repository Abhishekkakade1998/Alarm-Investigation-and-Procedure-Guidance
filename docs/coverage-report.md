# Test Coverage Report

## Run details

- Runtime: Python 3.13.14
- Test selection: `tests/unit`, `tests/integration`, and `tests/mcp`
- Result: 42 passed
- Command:

  ```powershell
  python -m pytest tests\unit tests\integration tests\mcp -q --cov=. --cov-report=term
  ```

The full command reports 83% line coverage when the test files themselves are
included in the total. The application-source-only result is 74%; this is the
more meaningful implementation coverage figure.

## Application source coverage

| Component | Statements | Missed | Line coverage |
|---|---:|---:|---:|
| Copilot backend | 419 | 103 | 75% |
| Alarm API simulator | 347 | 71 | 80% |
| Streamlit frontend | 206 | 101 | 51% |
| Alarm MCP server | 250 | 43 | 83% |
| RAG ingestion/retrieval | 94 | 18 | 81% |
| **Source total** | **1,316** | **336** | **74%** |

The source-only report was produced with:

```powershell
coverage report --include="apps\backend\*.py,apps\frontend\*.py,rag\ingestion\*.py,mcp-servers\alarm-management\*.py"
```

Line coverage does not measure recommendation correctness, operational
suitability, or real-service availability. The frontend percentage is lower
because the suite uses unit-level Streamlit stubs rather than browser-driven
interaction. The e2e smoke tests are excluded because they require live local
services.
