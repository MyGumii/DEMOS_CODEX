"""Package the public Hermes client and install guide; never copy server data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "static/integrations/hermes"


def artifacts():
    adapter = (ROOT / "agent_integrations/hermes_mcp.py").read_bytes()
    guide = (ROOT / "integrations/hermes/client_install.md").read_bytes()
    skill = (ROOT / "integrations/hermes/second-brain/SKILL.md").read_bytes()
    manifest = dict(schema_version=1, filename="hermes_mcp.py",
                    sha256=hashlib.sha256(adapter).hexdigest(), size_bytes=len(adapter),
                    python_min="3.10", mcp="2.x", contract_version="agent-knowledge-loop-v1",
                    knowledge_sets_version=2,
                    skill=dict(name="second-brain", filename="second-brain/SKILL.md",
                               sha256=hashlib.sha256(skill).hexdigest(), size_bytes=len(skill)))
    return {"hermes_mcp.py": adapter, "install.md": guide, "second-brain/SKILL.md": skill,
            "manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check packaged assets without writing")
    args = parser.parse_args()
    expected = artifacts()
    if args.check:
        stale = [name for name, content in expected.items()
                 if not (DESTINATION / name).is_file() or (DESTINATION / name).read_bytes() != content]
        if stale:
            parser.exit(1, "Stale Hermes client assets: " + ", ".join(stale) + "\n")
    else:
        DESTINATION.mkdir(parents=True, exist_ok=True)
        for name, content in expected.items():
            target = DESTINATION / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.read_bytes() != content:
                temporary = target.with_name(target.name + ".packaging-tmp")
                temporary.write_bytes(content)
                temporary.replace(target)
    print("Hermes client assets: " + ("current" if args.check else "packaged"))


if __name__ == "__main__":
    main()
