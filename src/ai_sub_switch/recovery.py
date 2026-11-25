import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime

from .domain import BlockedTurn, ModelChoice


def quota_failure(events):
    rejection = None
    terminal = False
    for event in events:
        if event.get("parent_tool_use_id") or event.get("agent_id"):
            continue
        info = event.get("rate_limit_info") or {}
        if (
            event.get("type") == "rate_limit_event"
            and info.get("status") == "rejected"
            and info.get("rateLimitType") in {"five_hour", "seven_day"}
        ):
            reset = info.get("resetsAt")
            if type(reset) is int and 0 < reset < 4_102_444_800:
                rejection = datetime.fromtimestamp(reset, UTC)
        if (
            event.get("type") == "assistant"
            and event.get("error") == "rate_limit"
            and event.get("api_error_status") == 429
        ):
            terminal = True
    return rejection if terminal else None


def _decode(payload):
    row = json.loads(payload)
    row["source"] = ModelChoice(**row["source"])
    for key in ("observed_at", "resets_at"):
        if row[key]:
            row[key] = datetime.fromisoformat(row[key])
    return BlockedTurn(**row)


class Queue:
    def __init__(self, path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS recovery (
              session TEXT NOT NULL, turn TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
              PRIMARY KEY (session, turn)
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def get(self, key):
        row = self.db.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value)
            )

    def add(self, turn):
        payload = asdict(turn)
        for key in ("observed_at", "resets_at"):
            if payload[key]:
                payload[key] = payload[key].isoformat()
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO recovery(session,turn,payload,state) VALUES(?,?,?,'pending')",
                (turn.session_id, turn.turn_id, json.dumps(payload)),
            )

    def pending(self):
        return [
            _decode(row[0])
            for row in self.db.execute(
                "SELECT payload FROM recovery WHERE state='pending' ORDER BY rowid"
            )
        ]

    def status(self):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT session,turn,state,detail FROM recovery ORDER BY rowid"
            )
        ]

    def transition(self, turn, old, new, detail=""):
        with self.db:
            row = self.db.execute(
                "UPDATE recovery SET state=?,detail=? WHERE session=? AND turn=? AND state=?",
                (new, detail, turn.session_id, turn.turn_id, old),
            )
            return row.rowcount == 1


class Conductor:
    def __init__(self, path):
        self.path = path.resolve()

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        return closing(db)

    def busy(self):
        with self._connect() as db:
            return bool(
                db.execute("""
                SELECT 1 FROM sessions s JOIN workspaces w ON w.local_id=s.workspace_id
                WHERE s.agent_type='claude' AND w.state='ready' AND
                COALESCE(s.is_hidden,0)=0 AND s.status IN ('working','waiting') LIMIT 1
            """).fetchone()
            )

    def blocked(self, session_id, since, account_id):
        with self._connect() as db:
            row = db.execute(
                """
                SELECT s.*, w.state workspace_state
                FROM sessions s JOIN workspaces w ON w.local_id=s.workspace_id WHERE s.id=?
            """,
                (session_id,),
            ).fetchone()
            if (
                not row
                or row["agent_type"] != "claude"
                or row["status"] not in {"idle", "error"}
                or row["workspace_state"] != "ready"
                or row["is_hidden"]
            ):
                return None
            message = db.execute(
                """
                SELECT turn_id,cancelled_at,sent_at FROM session_messages
                WHERE role='user' AND session_id=?
                ORDER BY COALESCE(sent_at,created_at) DESC,rowid DESC LIMIT 1
            """,
                (session_id,),
            ).fetchone()
            if (
                not message
                or not message["sent_at"]
                or message["cancelled_at"]
                or not message["turn_id"]
            ):
                return None
            events = db.execute(
                """
                SELECT event_id,received_at,content FROM session_events
                WHERE session_id=? AND event_type='agent' AND reverted_at IS NULL
                ORDER BY session_index DESC LIMIT 500
            """,
                (session_id,),
            ).fetchall()
        selected = []
        for item in reversed(events):
            body = json.loads(item["content"])
            if body.get("turnId") == message["turn_id"] and isinstance(
                body.get("rawPayload"), dict
            ):
                selected.append((item, body["rawPayload"]))
        reset = quota_failure([value for _, value in selected])
        if reset is None:
            return None
        item = next(
            item
            for item, value in reversed(selected)
            if value.get("type") == "assistant"
            and value.get("error") == "rate_limit"
            and not value.get("parent_tool_use_id")
            and not value.get("agent_id")
        )
        observed = datetime.fromisoformat(item["received_at"])
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        if observed < since:
            return None
        return BlockedTurn(
            row["workspace_id"],
            session_id,
            message["turn_id"],
            item["event_id"],
            account_id,
            ModelChoice("claude", row["model"], row["claude_effort_level"]),
            observed,
            reset,
        )

    def sessions(self):
        with self._connect() as db:
            return [
                row[0]
                for row in db.execute("""
                SELECT s.id FROM sessions s JOIN workspaces w ON w.local_id=s.workspace_id
                WHERE w.state='ready' AND s.agent_type='claude' AND COALESCE(s.is_hidden,0)=0
                AND s.status IN ('idle','error')
            """)
            ]

    def title(self, turn):
        with self._connect() as db:
            names = db.execute(
                """
                SELECT id,title FROM sessions WHERE workspace_id=? AND COALESCE(is_hidden,0)=0
            """,
                (turn.workspace_id,),
            ).fetchall()
        title = next(
            (row["title"] for row in names if row["id"] == turn.session_id), None
        )
        if not title or sum(row["title"] == title for row in names) != 1:
            raise RuntimeError("Source chat title is missing or ambiguous")
        return title

    def receipt(self, session, marker, source_session):
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT content,full_message FROM session_messages
                WHERE session_id=? AND role='user' AND sent_at IS NOT NULL
            """,
                (session,),
            )
            attachment = f"session-transcript-{source_session}.md"
            return any(
                marker in f"{row[0] or ''} {row[1] or ''}"
                and attachment in f"{row[0] or ''} {row[1] or ''}"
                for row in rows
            )
