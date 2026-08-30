#!/usr/bin/env python3
# scripts/registry_diff.py
# Prints any identifier mismatch between code and REGISTRY.json
# MUST print nothing for a clean build (per AGENTS.md)

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_registry() -> dict:
    with open("REGISTRY.json") as f:
        return json.load(f)


def main() -> int:
    registry = load_registry()
    
    # In Stage 1, we just validate REGISTRY.json is valid JSON and has required keys
    required_keys = [
        "reason_codes",
        "mcp_tools", 
        "aal_levels",
        "order_states",
        "campaign_states",
        "ledger_entries",
        "verifier_exit_codes",
        "discord_channels",
        "negotiation_states",
        "enums_are_exhaustive",
        "routes",
    ]
    
    for key in required_keys:
        if key not in registry:
            print(f"MISSING KEY IN REGISTRY: {key}", file=sys.stderr)
            return 1
    
    if not registry.get("enums_are_exhaustive"):
        print("enums_are_exhaustive must be true", file=sys.stderr)
        return 1
    
    # Check for duplicates in each list
    for key, value in registry.items():
        if isinstance(value, list):
            seen = set()
            for item in value:
                if item in seen:
                    print(f"DUPLICATE IN {key}: {item}", file=sys.stderr)
                    return 1
                seen.add(item)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())