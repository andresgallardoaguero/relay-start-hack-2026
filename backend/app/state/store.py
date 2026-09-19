# Script: store.py
# Purpose: Keep every decision, customer answer, mandate and run of this service in one SQLite file, so a purchase is recorded exactly once and survives a restart
# Author: Jonas Lüthi
# Date: September 2026

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path









#### Step 1: Define the tables ####

# Create the five tables of the plan, where the live purchase identifier is the primary key of a decision
TABLE_DEFINITIONS = (
    """
    CREATE TABLE IF NOT EXISTS decisions (
        live_authorization_id TEXT PRIMARY KEY,
        sequence INTEGER NOT NULL,
        run_id TEXT,
        source_authorization_id TEXT,
        scenario_id TEXT,
        replay_order INTEGER,
        mandate_id TEXT,
        sim_timestamp TEXT,
        amount_chf TEXT,
        currency TEXT,
        merchant_id TEXT,
        merchant_name TEXT,
        decision TEXT NOT NULL,
        status TEXT NOT NULL,
        reason_codes_json TEXT NOT NULL,
        customer_message TEXT NOT NULL,
        notes_json TEXT NOT NULL,
        received_at TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        deadline_at TEXT,
        margin_ms REAL,
        total_ms REAL,
        posted INTEGER NOT NULL,
        post_status_code INTEGER,
        post_error TEXT,
        delivery_count INTEGER NOT NULL,
        human_deadline_at TEXT,
        source TEXT NOT NULL,
        trace_json TEXT,
        problems_json TEXT,
        resolution_json TEXT,
        trust_score_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS resolutions (
        live_authorization_id TEXT PRIMARY KEY,
        decision TEXT NOT NULL,
        customer_message TEXT NOT NULL,
        resolved_at TEXT NOT NULL,
        posted INTEGER NOT NULL,
        post_status_code INTEGER,
        post_error TEXT,
        response_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mandates (
        mandate_id TEXT PRIMARY KEY,
        draft_id TEXT,
        instruction TEXT NOT NULL,
        hard_rules_json TEXT NOT NULL,
        uncertainty_policy TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        raw_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        scenario_id TEXT NOT NULL,
        mandate_id TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        raw_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kv (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
)



# Name the columns of a decision that are stored as JSON text
DECISION_JSON_COLUMNS = {
    "reason_codes_json": "reason_codes",
    "notes_json": "notes",
    "trace_json": "trace",
    "problems_json": "problems",
    "resolution_json": "resolution",
    "trust_score_json": "trust_score",
}



# Name the columns added after the first version of the decisions table, so an older store file gains them on opening
DECISION_COLUMNS_ADDED_LATER = (
    ("trust_score_json", "TEXT"),
)



# Name the status a purchase can have in this store
DECISION_STATUSES = ("approved", "declined", "pending", "expired", "cancelled")









#### Step 2: Convert between records and rows ####

# Write a moment as ISO 8601 text in UTC
def format_moment(moment):
    return moment.astimezone(timezone.utc).isoformat()



# Read a moment from ISO 8601 text
def read_moment(moment_text):
    return datetime.fromisoformat(moment_text.replace("Z", "+00:00"))



# Turn one decision record into the row of the decisions table
def decision_record_to_row(record):
    return {
        "live_authorization_id": record["live_authorization_id"],
        "sequence": record["sequence"],
        "run_id": record.get("run_id"),
        "source_authorization_id": record.get("source_authorization_id"),
        "scenario_id": record.get("scenario_id"),
        "replay_order": record.get("replay_order"),
        "mandate_id": record.get("mandate_id"),
        "sim_timestamp": record.get("sim_timestamp"),
        "amount_chf": record.get("amount_chf"),
        "currency": record.get("currency"),
        "merchant_id": record.get("merchant_id"),
        "merchant_name": record.get("merchant_name"),
        "decision": record["decision"],
        "status": record["status"],
        "reason_codes_json": json.dumps(record.get("reason_codes", [])),
        "customer_message": record.get("customer_message", ""),
        "notes_json": json.dumps(record.get("notes", [])),
        "received_at": record["received_at"],
        "decided_at": record["decided_at"],
        "deadline_at": record.get("deadline_at"),
        "margin_ms": record.get("margin_ms"),
        "total_ms": record.get("total_ms"),
        "posted": 1 if record.get("posted") else 0,
        "post_status_code": record.get("post_status_code"),
        "post_error": record.get("post_error"),
        "delivery_count": record.get("delivery_count", 1),
        "human_deadline_at": record.get("human_deadline_at"),
        "source": record.get("source", "live"),
        "trace_json": json.dumps(record["trace"]) if record.get("trace") is not None else None,
        "problems_json": json.dumps(record["problems"]) if record.get("problems") is not None else None,
        "resolution_json": json.dumps(record["resolution"]) if record.get("resolution") is not None else None,
        "trust_score_json": json.dumps(record["trust_score"]) if record.get("trust_score") is not None else None,
    }



# Turn one row of the decisions table back into a decision record
def decision_row_to_record(row):
    record = dict(row)
    for column_name, field_name in DECISION_JSON_COLUMNS.items():
        json_text = record.pop(column_name)
        record[field_name] = json.loads(json_text) if json_text is not None else None
    record["posted"] = bool(record["posted"])
    if record["reason_codes"] is None:
        record["reason_codes"] = []
    if record["notes"] is None:
        record["notes"] = []
    return record









#### Step 3: Define the store ####

# Keep the state of the service in one SQLite file with write-ahead logging, which survives a restart and is one file to wipe
class DecisionStore:

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents = True, exist_ok = True)
        self.connection = sqlite3.connect(str(self.db_path), check_same_thread = False)
        self.connection.row_factory = sqlite3.Row
        if str(self.db_path) != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        for table_definition in TABLE_DEFINITIONS:
            self.connection.execute(table_definition)
        self.add_missing_decision_columns()
        self.connection.commit()



    # Add every column of the decisions table that an older store file does not have yet, which keeps its rows readable
    def add_missing_decision_columns(self):
        existing_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(decisions)").fetchall()}
        for column_name, column_type in DECISION_COLUMNS_ADDED_LATER:
            if column_name not in existing_columns:
                self.connection.execute("ALTER TABLE decisions ADD COLUMN " + column_name + " " + column_type)



    # Close the file
    def close(self):
        self.connection.close()



    # Take the next position in the order of arrival
    def next_sequence(self):
        row = self.connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM decisions").fetchone()
        return int(row["next_sequence"])









    #### Step 4: Keep the decisions ####

    # Read the record of one live purchase, or None when the purchase was never seen
    def get_decision(self, live_authorization_id):
        row = self.connection.execute(
            "SELECT * FROM decisions WHERE live_authorization_id = ?",
            (live_authorization_id,),
        ).fetchone()
        if row is None:
            return None
        return decision_row_to_record(row)



    # Write the record of one live purchase, where a record without a sequence takes the next one
    def save_decision(self, record):
        record = dict(record)
        if record.get("sequence") is None:
            record["sequence"] = self.next_sequence()
        row = decision_record_to_row(record)
        column_names = ", ".join(row.keys())
        placeholders = ", ".join("?" for column_name in row)
        self.connection.execute(
            "INSERT OR REPLACE INTO decisions (" + column_names + ") VALUES (" + placeholders + ")",
            tuple(row.values()),
        )
        self.connection.commit()
        return self.get_decision(record["live_authorization_id"])



    # Count one more delivery of a purchase that was already recorded, and return the new count
    def note_redelivery(self, live_authorization_id):
        self.connection.execute(
            "UPDATE decisions SET delivery_count = delivery_count + 1 WHERE live_authorization_id = ?",
            (live_authorization_id,),
        )
        self.connection.commit()
        record = self.get_decision(live_authorization_id)
        return record["delivery_count"]



    # Record whether the decision reached the platform
    def mark_posted(self, live_authorization_id, posted, status_code = None, error_text = None):
        self.connection.execute(
            "UPDATE decisions SET posted = ?, post_status_code = ?, post_error = ? WHERE live_authorization_id = ?",
            (1 if posted else 0, status_code, error_text, live_authorization_id),
        )
        self.connection.commit()



    # Change the status of a purchase, which is how a question becomes an approval, a refusal or an expired question
    def set_status(self, live_authorization_id, status, resolution = None):
        assert status in DECISION_STATUSES, "Unknown status " + str(status)
        resolution_json = json.dumps(resolution) if resolution is not None else None
        self.connection.execute(
            "UPDATE decisions SET status = ?, resolution_json = COALESCE(?, resolution_json) WHERE live_authorization_id = ?",
            (status, resolution_json, live_authorization_id),
        )
        self.connection.commit()



    # List the decisions in order of arrival, for one run or for all, where a limit keeps the newest ones
    def list_decisions(self, run_id = None, limit = None):
        query = "SELECT * FROM decisions"
        parameters = ()
        if run_id is not None:
            query = query + " WHERE run_id = ?"
            parameters = (run_id,)
        query = query + " ORDER BY sequence DESC"
        if limit is not None:
            query = query + " LIMIT ?"
            parameters = parameters + (int(limit),)
        rows = self.connection.execute(query, parameters).fetchall()
        return [decision_row_to_record(row) for row in reversed(rows)]



    # List the questions that still wait for the customer, oldest first
    def list_pending(self):
        rows = self.connection.execute(
            "SELECT * FROM decisions WHERE status = 'pending' ORDER BY sequence ASC",
        ).fetchall()
        return [decision_row_to_record(row) for row in rows]



    # Mark every question whose answer window has passed as expired, and return the changed records
    def expire_pending(self, now):
        expired_records = [
            record
            for record in self.list_pending()
            if record.get("human_deadline_at") is not None and read_moment(record["human_deadline_at"]) <= now
        ]
        for record in expired_records:
            self.set_status(record["live_authorization_id"], "expired")
        return [self.get_decision(record["live_authorization_id"]) for record in expired_records]



    # Count the purchases per status
    def count_by_status(self):
        rows = self.connection.execute("SELECT status, COUNT(*) AS purchases FROM decisions GROUP BY status").fetchall()
        counts = {status: 0 for status in DECISION_STATUSES}
        for row in rows:
            counts[row["status"]] = int(row["purchases"])
        return counts









    #### Step 5: Keep the customer's answers ####

    # Write the customer's answer to one question
    def save_resolution(self, live_authorization_id, resolution):
        self.connection.execute(
            """
            INSERT OR REPLACE INTO resolutions
            (live_authorization_id, decision, customer_message, resolved_at, posted, post_status_code, post_error, response_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                live_authorization_id,
                resolution["decision"],
                resolution.get("customer_message", ""),
                resolution["resolved_at"],
                1 if resolution.get("posted") else 0,
                resolution.get("post_status_code"),
                resolution.get("post_error"),
                json.dumps(resolution.get("response")) if resolution.get("response") is not None else None,
            ),
        )
        self.connection.commit()



    # Read the customer's answer to one question, or None
    def get_resolution(self, live_authorization_id):
        row = self.connection.execute(
            "SELECT * FROM resolutions WHERE live_authorization_id = ?",
            (live_authorization_id,),
        ).fetchone()
        if row is None:
            return None
        resolution = dict(row)
        resolution["posted"] = bool(resolution["posted"])
        response_json = resolution.pop("response_json")
        resolution["response"] = json.loads(response_json) if response_json is not None else None
        return resolution









    #### Step 6: Keep the mandates and the runs ####

    # Write one mandate as the platform confirmed it
    def save_mandate(self, mandate):
        now_text = format_moment(datetime.now(timezone.utc))
        existing = self.get_mandate(mandate["mandate_id"])
        created_at = existing["created_at"] if existing is not None else mandate.get("created_at", now_text)
        self.connection.execute(
            """
            INSERT OR REPLACE INTO mandates
            (mandate_id, draft_id, instruction, hard_rules_json, uncertainty_policy, status, created_at, updated_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mandate["mandate_id"],
                mandate.get("draft_id"),
                mandate["instruction"],
                json.dumps(mandate.get("hard_rules", [])),
                mandate["uncertainty_policy"],
                mandate.get("status", "active"),
                created_at,
                now_text,
                json.dumps(mandate.get("raw")) if mandate.get("raw") is not None else None,
            ),
        )
        self.connection.commit()
        return self.get_mandate(mandate["mandate_id"])



    # Read one mandate, or None
    def get_mandate(self, mandate_id):
        row = self.connection.execute("SELECT * FROM mandates WHERE mandate_id = ?", (mandate_id,)).fetchone()
        if row is None:
            return None
        return mandate_row_to_record(row)



    # List every mandate, newest first
    def list_mandates(self):
        rows = self.connection.execute("SELECT * FROM mandates ORDER BY created_at DESC").fetchall()
        return [mandate_row_to_record(row) for row in rows]



    # Write one run as the platform started it
    def save_run(self, run):
        now_text = format_moment(datetime.now(timezone.utc))
        existing = self.get_run(run["run_id"])
        started_at = existing["started_at"] if existing is not None else run.get("started_at", now_text)
        self.connection.execute(
            """
            INSERT OR REPLACE INTO runs
            (run_id, scenario_id, mandate_id, status, started_at, updated_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["run_id"],
                run["scenario_id"],
                run["mandate_id"],
                run.get("status", "active"),
                started_at,
                now_text,
                json.dumps(run.get("raw")) if run.get("raw") is not None else None,
            ),
        )
        self.connection.commit()
        return self.get_run(run["run_id"])



    # Read one run, or None
    def get_run(self, run_id):
        row = self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return run_row_to_record(row)



    # List the runs, newest first, or only the runs that are still open
    def list_runs(self, active_only = False):
        query = "SELECT * FROM runs"
        if active_only:
            query = query + " WHERE status = 'active'"
        rows = self.connection.execute(query + " ORDER BY started_at DESC").fetchall()
        return [run_row_to_record(row) for row in rows]









    #### Step 7: Keep single values and wipe the state ####

    # Read one named value, or the default
    def get_value(self, key, default = None):
        row = self.connection.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None or row["value"] is None:
            return default
        return json.loads(row["value"])



    # Write one named value
    def set_value(self, key, value):
        self.connection.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.connection.commit()



    # Empty every table, which is what a team reset on the platform calls for
    def clear_all(self):
        for table_name in ("decisions", "resolutions", "mandates", "runs", "kv"):
            self.connection.execute("DELETE FROM " + table_name)
        self.connection.commit()









#### Step 8: Convert mandate and run rows ####

# Turn one row of the mandates table into a record
def mandate_row_to_record(row):
    record = dict(row)
    record["hard_rules"] = json.loads(record.pop("hard_rules_json"))
    raw_json = record.pop("raw_json")
    record["raw"] = json.loads(raw_json) if raw_json is not None else None
    return record



# Turn one row of the runs table into a record
def run_row_to_record(row):
    record = dict(row)
    raw_json = record.pop("raw_json")
    record["raw"] = json.loads(raw_json) if raw_json is not None else None
    return record
