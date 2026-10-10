"""Subprocess-only NexAU configuration load smoke."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nexau import AgentConfig


def main() -> int:
    path = Path(sys.argv[1]).resolve()
    config = AgentConfig.from_yaml(path)
    print(
        json.dumps(
            {
                "ok": True,
                "name": config.name,
                "tool_call_mode": config.tool_call_mode,
                "tool_count": len(config.tools or []),
                "skill_count": len(config.skills or []),
                "sub_agent_count": len(config.sub_agents or {}),
                "middleware_count": len(config.middlewares or []),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
