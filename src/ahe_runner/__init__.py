"""Reusable Agentic Harness Engineering runner."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ahe-runner")
except PackageNotFoundError:
    __version__ = "0+unknown"

from .adapter import (
    BenchmarkAdapter,
    BenchmarkAdapterV2,
    BenchmarkInterface,
    BenchmarkToolSpec,
)
from .child_process import run_evaluated_trial_in_child
from .config import ResolvedConfig, load_config
from .controller import CampaignController
from .runtime import (
    AgentInput,
    AgentInputMessage,
    AgentOutput,
    AgentSessionRequest,
    EvaluatedAgentRuntime,
    EvaluatedAgentSession,
    RuntimeProcessSpec,
    ToolTiming,
)

__all__ = [
    "AgentInput",
    "AgentInputMessage",
    "AgentOutput",
    "AgentSessionRequest",
    "BenchmarkAdapter",
    "BenchmarkAdapterV2",
    "BenchmarkInterface",
    "BenchmarkToolSpec",
    "CampaignController",
    "EvaluatedAgentRuntime",
    "EvaluatedAgentSession",
    "ResolvedConfig",
    "RuntimeProcessSpec",
    "ToolTiming",
    "load_config",
    "run_evaluated_trial_in_child",
]
