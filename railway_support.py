from __future__ import annotations

import json
import sqlite3
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase


POLICIES = (
    {
        "category": "delay",
        "title": "Train delays",
        "guidance": "Check the train's live running status through an official railway channel. Ask station staff about current platform and onward-travel options; do not rely on an estimated time in this demo.",
    },
    {
        "category": "accessibility",
        "title": "Accessibility assistance",
        "guidance": "Contact the station assistance desk or Station Master and confirm the available boarding and platform assistance for this journey.",
    },
    {
        "category": "refund",
        "title": "Cancellation and refunds",
        "guidance": "Refund eligibility depends on ticket type and cancellation timing. Check the official booking channel before cancelling; this demo cannot determine an entitlement.",
    },
    {
        "category": "booking",
        "title": "Bookings and PNRs",
        "guidance": "Verify booking status and passenger details using the official railway booking channel and the PNR on the ticket.",
    },
    {
        "category": "general",
        "title": "General railway support",
        "guidance": "For live operational information or urgent help, contact railway staff or an official railway support channel.",
    },
)


def classify_issue(text: str) -> str:
    normalized = text.casefold()
    if any(word in normalized for word in ("refund", "cancel", "cancellation", "money back")):
        return "refund"
    if any(word in normalized for word in ("wheelchair", "accessible", "accessibility", "ramp", "assistance", "disability")):
        return "accessibility"
    if any(word in normalized for word in ("delay", "late", "delayed", "running", "rescheduled")):
        return "delay"
    if any(word in normalized for word in ("booking", "pnr", "ticket", "reservation")):
        return "booking"
    return "general"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return str(uuid.uuid4())


class SQLiteRailwayStore:
    """Persistent local preview store used when Aura is not configured or reachable."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.initialize()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS passengers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                passenger_id TEXT NOT NULL REFERENCES passengers(id),
                pnr TEXT NOT NULL,
                train_number TEXT NOT NULL,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                travel_date TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS issues (
                id TEXT PRIMARY KEY,
                passenger_id TEXT NOT NULL REFERENCES passengers(id),
                trip_id TEXT NOT NULL REFERENCES trips(id),
                category TEXT NOT NULL,


                description TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS support_needs (
                passenger_id TEXT PRIMARY KEY REFERENCES passengers(id),
                detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interactions (
                id TEXT PRIMARY KEY,
                passenger_id TEXT NOT NULL REFERENCES passengers(id),
                issue_id TEXT REFERENCES issues(id),
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                response TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policies (
                category TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                guidance TEXT NOT NULL
            );
            """
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO policies(category, title, guidance) VALUES (?, ?, ?)",
            [(item["category"], item["title"], item["guidance"]) for item in POLICIES],
        )
        self.connection.commit()

    def teach(
        self,
        passenger_id: str,
        passenger_name: str,
        pnr: str,
        train_number: str,
        origin: str,
        destination: str,
        travel_date: str,
        issue_description: str,
        support_need: str = "",
    ) -> str:
        trip_id = _new_id()
        issue_id = _new_id()
        category = classify_issue(issue_description)
        now = _now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO passengers(id, name) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                (passenger_id, passenger_name),
            )
            self.connection.execute(
                "INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (trip_id, passenger_id, pnr, train_number, origin, destination, travel_date, now),
            )
            self.connection.execute(
                "INSERT INTO issues VALUES (?, ?, ?, ?, ?, ?, ?)",
                (issue_id, passenger_id, trip_id, category, issue_description, "Open", now),
            )
            self.connection.execute(
                "INSERT INTO interactions(id, passenger_id, issue_id, kind, message, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (_new_id(), passenger_id, issue_id, "intake", issue_description, now),
            )
            if support_need:
                self.connection.execute(
                    "INSERT INTO support_needs(passenger_id, detail) VALUES (?, ?) "
                    "ON CONFLICT(passenger_id) DO UPDATE SET detail = excluded.detail",
                    (passenger_id, support_need),
                )
        return issue_id

    def get_context(self, passenger_id: str, question: str = "") -> dict[str, Any]:
        passenger = self.connection.execute(
            "SELECT id, name FROM passengers WHERE id = ?", (passenger_id,)
        ).fetchone()
        if passenger is None:
            return {"passenger": None, "trip": None, "issue": None, "support_need": "", "policies": [], "interactions": []}

        trip = self.connection.execute(
            "SELECT pnr, train_number, origin, destination, travel_date FROM trips "
            "WHERE passenger_id = ? ORDER BY updated_at DESC LIMIT 1",
            (passenger_id,),
        ).fetchone()
        issue = self.connection.execute(
            "SELECT id, category, description, status, created_at FROM issues "
            "WHERE passenger_id = ? ORDER BY created_at DESC LIMIT 1",
            (passenger_id,),
        ).fetchone()
        need = self.connection.execute(
            "SELECT detail FROM support_needs WHERE passenger_id = ?", (passenger_id,)
        ).fetchone()
        categories = {classify_issue(question)}
        if issue:
            categories.add(issue["category"])
        if need and need["detail"]:
            categories.add("accessibility")
        policy_rows = self.connection.execute(
            "SELECT category, title, guidance FROM policies WHERE category IN ("
            + ",".join("?" for _ in categories)
            + ")",
            tuple(categories),
        ).fetchall()
        interactions = self.connection.execute(
            "SELECT kind, message, response, created_at FROM interactions "
            "WHERE passenger_id = ? ORDER BY created_at DESC LIMIT 8",
            (passenger_id,),
        ).fetchall()
        return {
            "passenger": dict(passenger),
            "trip": dict(trip) if trip else None,
            "issue": dict(issue) if issue else None,
            "support_need": need["detail"] if need else "",
            "policies": [dict(row) for row in policy_rows],
            "interactions": [dict(row) for row in interactions],
        }

    def record_followup(self, passenger_id: str, issue_id: str | None, question: str, response: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO interactions(id, passenger_id, issue_id, kind, message, response, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_new_id(), passenger_id, issue_id, "followup", question, response, _now()),
            )

    def close(self) -> None:
        self.connection.close()


class Neo4jRailwayStore:
    """Stores passenger memory as connected entities and retrieves it by traversal."""

    def __init__(self, uri: str, username: str, password: str, database: str) -> None:
        self.database = database
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        try:
            self.driver.verify_connectivity()
            self.initialize()
        except Exception:
            self.driver.close()
            raise

    def initialize(self) -> None:
        constraints = (
            "CREATE CONSTRAINT railway_passenger_id IF NOT EXISTS FOR (n:Passenger) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT railway_trip_id IF NOT EXISTS FOR (n:Trip) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT railway_issue_id IF NOT EXISTS FOR (n:Issue) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT railway_policy_category IF NOT EXISTS FOR (n:Policy) REQUIRE n.category IS UNIQUE",
        )
        with self.driver.session(database=self.database) as session:
            for query in constraints:
                session.run(query).consume()
            for policy in POLICIES:
                session.run(
                    "MERGE (p:Policy {category: $category}) "
                    "SET p.title = $title, p.guidance = $guidance",
                    **policy,
                ).consume()

    @staticmethod
    def _teach_transaction(tx: Any, values: dict[str, str]) -> None:
        tx.run(
            "MERGE (p:Passenger {id: $passenger_id}) "
            "SET p.name = $passenger_name, p.updatedAt = datetime() "
            "MERGE (t:Trip {id: $trip_id}) "
            "SET t.pnr = $pnr, t.trainNumber = $train_number, t.origin = $origin, "
            "t.destination = $destination, t.travelDate = $travel_date, t.updatedAt = datetime() "
            "MERGE (origin:Station {name: $origin}) "
            "MERGE (destination:Station {name: $destination}) "
            "MERGE (route:RailRoute {id: $route_id}) "
            "SET route.origin = $origin, route.destination = $destination "
            "MERGE (i:Issue {id: $issue_id}) "
            "SET i.category = $category, i.description = $issue_description, "
            "i.status = 'Open', i.createdAt = datetime() "
            "MERGE (p)-[:HAS_TRIP]->(t) "
            "MERGE (t)-[:ORIGIN]->(origin) "
            "MERGE (t)-[:DESTINATION]->(destination) "
            "MERGE (t)-[:ON_ROUTE]->(route) "
            "MERGE (route)-[:STOPS_AT {sequence: 0}]->(origin) "
            "MERGE (route)-[:STOPS_AT {sequence: 1}]->(destination) "
            "MERGE (p)-[:REPORTED]->(i) "
            "MERGE (t)-[:HAS_ISSUE]->(i) "
            "WITH p, t, i "
            "CREATE (interaction:SupportInteraction {id: $interaction_id, kind: 'intake', "
            "message: $issue_description, createdAt: datetime()}) "
            "MERGE (p)-[:HAD_INTERACTION]->(interaction) "
            "MERGE (interaction)-[:ABOUT]->(i) "
            "MERGE (interaction)-[:ON_TRIP]->(t)",
            **values,
        ).consume()
        tx.run(
            "MATCH (i:Issue {id: $issue_id}), (policy:Policy {category: $category}) "
            "MERGE (i)-[:GUIDED_BY]->(policy)",
            issue_id=values["issue_id"],
            category=values["category"],
        ).consume()
        if values["support_need"]:
            tx.run(
                "MATCH (p:Passenger {id: $passenger_id}), (policy:Policy {category: 'accessibility'}) "
                "MERGE (need:SupportNeed {detail: $support_need}) "
                "MERGE (p)-[:HAS_SUPPORT_NEED]->(need) "
                "MERGE (need)-[:GUIDED_BY]->(policy)",
                passenger_id=values["passenger_id"],
                support_need=values["support_need"],
            ).consume()

    def teach(
        self,
        passenger_id: str,
        passenger_name: str,
        pnr: str,
        train_number: str,
        origin: str,
        destination: str,
        travel_date: str,
        issue_description: str,
        support_need: str = "",
    ) -> str:
        issue_id = _new_id()
        values = {
            "passenger_id": passenger_id,
            "passenger_name": passenger_name,
            "pnr": pnr,
            "train_number": train_number,
            "origin": origin,
            "destination": destination,
            "travel_date": travel_date,
            "issue_description": issue_description,
            "support_need": support_need,
            "trip_id": _new_id(),
            "issue_id": issue_id,
            "interaction_id": _new_id(),
            "route_id": f"{origin.casefold()}->{destination.casefold()}",
            "category": classify_issue(issue_description),
        }
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._teach_transaction, values)
        return issue_id

    def get_context(self, passenger_id: str, question: str = "") -> dict[str, Any]:
        records, _, _ = self.driver.execute_query(
            "MATCH (p:Passenger {id: $passenger_id}) RETURN p.id AS id, p.name AS name",
            passenger_id=passenger_id,
            database_=self.database,
        )
        if not records:
            return {"passenger": None, "trip": None, "issue": None, "support_need": "", "policies": [], "interactions": []}
        passenger = dict(records[0])
        trips, _, _ = self.driver.execute_query(
            "MATCH (:Passenger {id: $passenger_id})-[:HAS_TRIP]->(t:Trip) "
            "RETURN t.pnr AS pnr, t.trainNumber AS train_number, t.origin AS origin, "
            "t.destination AS destination, t.travelDate AS travel_date "
            "ORDER BY t.updatedAt DESC LIMIT 1",
            passenger_id=passenger_id,
            database_=self.database,
        )
        issues, _, _ = self.driver.execute_query(
            "MATCH (:Passenger {id: $passenger_id})-[:REPORTED]->(i:Issue) "
            "RETURN i.id AS id, i.category AS category, i.description AS description, "
            "i.status AS status, toString(i.createdAt) AS created_at "
            "ORDER BY i.createdAt DESC LIMIT 1",
            passenger_id=passenger_id,
            database_=self.database,
        )
        needs, _, _ = self.driver.execute_query(
            "MATCH (:Passenger {id: $passenger_id})-[:HAS_SUPPORT_NEED]->(n:SupportNeed) "
            "RETURN n.detail AS detail ORDER BY n.detail LIMIT 1",
            passenger_id=passenger_id,
            database_=self.database,
        )
        issue = dict(issues[0]) if issues else None
        support_need = needs[0]["detail"] if needs else ""
        policy_records, _, _ = self.driver.execute_query(
            "MATCH (p:Passenger {id: $passenger_id}) "
            "OPTIONAL MATCH (p)-[:REPORTED]->(:Issue)-[:GUIDED_BY]->(issuePolicy:Policy) "
            "OPTIONAL MATCH (p)-[:HAS_SUPPORT_NEED]->(:SupportNeed)-[:GUIDED_BY]->(needPolicy:Policy) "
            "OPTIONAL MATCH (questionPolicy:Policy {category: $question_category}) "
            "RETURN collect(DISTINCT issuePolicy) + collect(DISTINCT needPolicy) + "
            "collect(DISTINCT questionPolicy) AS policies",
            passenger_id=passenger_id,
            question_category=classify_issue(question) if question.strip() else "",
            database_=self.database,
        )
        policies = [dict(policy) for policy in policy_records[0]["policies"] if policy is not None]
        interactions, _, _ = self.driver.execute_query(
            "MATCH (:Passenger {id: $passenger_id})-[:HAD_INTERACTION]->(interaction:SupportInteraction) "
            "RETURN interaction.kind AS kind, interaction.message AS message, "
            "interaction.question AS question, interaction.response AS response, "
            "toString(interaction.createdAt) AS created_at "
            "ORDER BY interaction.createdAt DESC LIMIT 8",
            passenger_id=passenger_id,
            database_=self.database,
        )
        return {
            "passenger": passenger,
            "trip": dict(trips[0]) if trips else None,
            "issue": issue,
            "support_need": support_need,
            "policies": [dict(row) for row in policies],
            "interactions": [dict(row) for row in interactions],
        }

    @staticmethod
    def _record_transaction(tx: Any, values: dict[str, Any]) -> None:
        tx.run(
            "MATCH (p:Passenger {id: $passenger_id}) "
            "CREATE (interaction:SupportInteraction {id: $interaction_id, kind: 'followup', "
            "question: $question, response: $response, createdAt: datetime()}) "
            "MERGE (p)-[:HAD_INTERACTION]->(interaction)",
            **values,
        ).consume()
        if values["issue_id"]:
            tx.run(
                "MATCH (interaction:SupportInteraction {id: $interaction_id}), (i:Issue {id: $issue_id}) "
                "MERGE (interaction)-[:ABOUT]->(i)",
                interaction_id=values["interaction_id"],
                issue_id=values["issue_id"],
            ).consume()

    def record_followup(self, passenger_id: str, issue_id: str | None, question: str, response: str) -> None:
        values = {
            "passenger_id": passenger_id,
            "issue_id": issue_id,
            "question": question,
            "response": response,
            "interaction_id": _new_id(),
        }
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._record_transaction, values)

    def close(self) -> None:
        self.driver.close()


def build_response(question: str, context: dict[str, Any]) -> str:
    passenger = context.get("passenger")
    trip = context.get("trip")
    issue = context.get("issue")
    if not passenger or not trip or not issue:
        return (
            "I don't have a saved journey and support case for this passenger yet. "
            "Use **Teach the agent** to save the trip and issue, then ask your follow-up again."
        )

    answer = (
        f"Welcome back, {passenger['name']}. I found your saved journey: train "
        f"{trip['train_number']} (PNR {trip['pnr']}) from {trip['origin']} to "
        f"{trip['destination']} on {trip['travel_date']}. Your {issue['status'].lower()} "
        f"{issue['category']} case is: {issue['description']}"
    )
    policies = context.get("policies", [])
    requested_category = classify_issue(question)
    selected = next((p for p in policies if p["category"] == requested_category), None)
    if selected is None:
        selected = next((p for p in policies if p["category"] == issue["category"]), None)
    if selected:
        answer += f"\n\n**Suggested next step:** {selected['guidance']}"
    support_need = context.get("support_need", "")
    if support_need:
        answer += (
            f"\n\nI also remember your assistance request: {support_need}. "
            "Please mention it when speaking with station staff so they can check the arrangements for this trip."
        )
    answer += "\n\nLive train status and ticket entitlements are not checked by this prototype; confirm them through official railway channels."
    return answer


def generate_response(
    question: str,
    context: dict[str, Any],
    client: Any | None = None,
    model: str = "gpt-4o-mini",
) -> tuple[str, str]:
    fallback = build_response(question, context)
    if client is None:
        return fallback, "Local response"

    memory = {
        "passenger": context.get("passenger"),
        "trip": context.get("trip"),
        "issue": context.get("issue"),
        "support_need": context.get("support_need"),
        "verified_guidance": context.get("policies", []),
    }
    try:
        result = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a railway customer-support assistant. Use only the supplied passenger memory and guidance. "
                        "Never invent live train status, platform numbers, refund eligibility, or actions taken. "
                        "Give a concise next step and direct the passenger to official railway channels for live facts."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Passenger question: {question}\nSaved graph context: {json.dumps(memory, ensure_ascii=True)}",
                },
            ],
            max_completion_tokens=260,
        )
        content = result.choices[0].message.content
        if content and content.strip():
            return content.strip(), "OpenAI"
    except Exception:
        pass
    return fallback, "Local fallback"