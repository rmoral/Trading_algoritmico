"""Dump the FastAPI OpenAPI schema for the web API.

Used by `frontend/`'s `npm run gen:api` to regenerate the TypeScript
types whenever the API surface changes. The output path is
`frontend/openapi.json` so the npm script can find it relative to its
own CWD.

Run with: `uv run python scripts/export_openapi.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tradingbot_api.main import create_app

OUTPUT = Path(__file__).resolve().parent.parent / "frontend" / "openapi.json"


def main() -> int:
    app = create_app()
    schema = app.openapi()
    OUTPUT.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
