# Script: audit.py
# Purpose: Append every decision and every customer answer of a run to a file that is never rewritten
# Author: Jonas Lüthi
# Date: September 2026

import json
import re
from datetime import datetime, timezone
from pathlib import Path









#### Step 1: Name the file of a run ####

# Keep letters, digits, dashes and underscores of a run identifier, so it can be a file name on every system
UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9_-]+")



# Build the file name of a run, which is run_<id>.jsonl, without doubling the prefix when the identifier already carries it
def audit_file_name(run_id):
    safe_run_id = UNSAFE_CHARACTERS.sub("_", str(run_id) if run_id is not None else "unknown")
    if safe_run_id.startswith("run_"):
        return safe_run_id + ".jsonl"
    return "run_" + safe_run_id + ".jsonl"









#### Step 2: Define the audit log ####

# Append one JSON line per event to the file of its run, and never change a line once written
class AuditLog:

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents = True, exist_ok = True)



    # Locate the file of a run
    def path_for_run(self, run_id):
        return self.folder / audit_file_name(run_id)



    # Append one line with the moment of writing, the kind of event and its content
    def append(self, run_id, kind, payload):
        line = {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        with self.path_for_run(run_id).open("a", encoding = "utf-8") as audit_file:
            audit_file.write(json.dumps(line, ensure_ascii = False) + "\n")



    # Read every line of a run, oldest first
    def read_run(self, run_id):
        path = self.path_for_run(run_id)
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding = "utf-8").splitlines()
            if line.strip() != ""
        ]
