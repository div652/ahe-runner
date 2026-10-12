"""Example evolvable internal harness tool."""

from __future__ import annotations

from typing import Any


def record_note(note: str, agent_state: Any) -> dict[str, Any]:
    notes = list(agent_state.get_global_value("recorded_notes", []))
    notes.append(note)
    agent_state.set_global_value("recorded_notes", notes)
    return {"recorded": note, "note_count": len(notes)}
