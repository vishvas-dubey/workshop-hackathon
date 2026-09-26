import os
from datetime import date
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from railway_support import Neo4jRailwayStore, SQLiteRailwayStore, generate_response


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="RailAssist | Passenger Support", layout="wide")
st.session_state.setdefault("messages", [])
st.session_state.setdefault("passenger_id", "PAX-1042")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
    :root {
        --rail-blue: #213d77;
        --rail-blue-light: #eaf1fb;
        --rail-blue-border: #d3deef;
        --rail-orange: #ed7d31;
        --rail-ink: #20314c;
        --rail-muted: #61718a;
        --rail-canvas: #f4f7fc;
    }
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; color: var(--rail-ink); }
    .stApp { background: var(--rail-canvas); }
    h1, h2, h3 { font-family: 'Space Grotesk', sans-serif; letter-spacing: 0; color: var(--rail-blue); }
    main .block-container { max-width: 1480px; padding-top: 1.5rem; }
    [data-testid="stSidebar"] { background: #edf3fb; border-right: 1px solid var(--rail-blue-border); }
    [data-testid="stSidebar"] > div:first-child { border-top: 5px solid var(--rail-orange); }
    [data-testid="stSidebar"] h1 { color: var(--rail-blue); font-size: 1.4rem; }
    .rail-header {
        display: flex; align-items: center; justify-content: space-between; gap: 1rem;
        background: var(--rail-blue); color: white; padding: 1rem 1.25rem;
        border-radius: 6px 6px 0 0; border-bottom: 4px solid var(--rail-orange);
    }
    .rail-brand { font: 700 1.45rem 'Space Grotesk', sans-serif; }
    .rail-tagline { color: #dce7f7; font-size: 0.86rem; margin-top: 0.15rem; }
    .rail-chip { color: white; font-size: 0.74rem; font-weight: 700; letter-spacing: 0.04em; }
    .page-title { font: 700 1.8rem 'Space Grotesk', sans-serif; color: var(--rail-blue); margin: 1.2rem 0 0; }
    .page-subtitle { color: var(--rail-muted); margin: 0.2rem 0 1.1rem; }
    .status-pill { display: inline-block; padding: 0.25rem 0.55rem; border-radius: 4px; background: #dce8f8; color: var(--rail-blue); font-size: 0.78rem; font-weight: 700; }
    div[data-testid="stMetric"] { background: white; border: 1px solid var(--rail-blue-border); border-top: 3px solid var(--rail-orange); border-radius: 5px; padding: 0.7rem 0.85rem; }
    div[data-testid="stMetricLabel"] { color: var(--rail-muted); }
    .stButton > button[kind="primary"] { background: var(--rail-blue); border-color: var(--rail-blue); }
    .stButton > button[kind="primary"]:hover { background: #172e60; border-color: var(--rail-orange); }
    button[role="tab"][aria-selected="true"] { color: var(--rail-blue); border-bottom-color: var(--rail-orange); }
    [data-testid="stChatMessage"] { border-radius: 6px; border: 1px solid var(--rail-blue-border); background: white; }
    [data-testid="stChatInput"] { border-color: var(--rail-blue-border); }
    @media (max-width: 700px) {
        .rail-chip { display: none; }
        .rail-header { padding: 0.8rem 1rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_store():
    uri = os.getenv("NEO4J_URI", "").strip()
    username = os.getenv("NEO4J_USERNAME", "").strip()
    password = os.getenv("NEO4J_PASSWORD", "").strip()
    database = os.getenv("NEO4J_DATABASE", "neo4j").strip() or "neo4j"
    if uri and username and password:
        try:
            return Neo4jRailwayStore(uri, username, password, database), "Neo4j Aura", ""
        except Exception as error:
            preview = SQLiteRailwayStore(ROOT / ".railway_demo.sqlite3")
            return preview, "Local preview", type(error).__name__
    preview = SQLiteRailwayStore(ROOT / ".railway_demo.sqlite3")
    return preview, "Local preview", "No complete NEO4J_* connection settings were found."


@st.cache_resource
def get_llm_client():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    return OpenAI(api_key=api_key) if api_key else None


store, storage_name, storage_note = get_store()
llm_client = get_llm_client()

with st.sidebar:
    st.markdown("# RailAssist")
    st.caption("Passenger support with journey memory")
    st.markdown(f"<span class='status-pill'>{storage_name}</span>", unsafe_allow_html=True)
    st.caption("Response engine: OpenAI" if llm_client else "Response engine: local memory-guided")
    if storage_name == "Local preview":
        st.warning(
            f"{storage_note} This preview saves memory locally. Configure write-enabled Neo4j credentials in `.env` to demonstrate the graph database."
        )

    previous_passenger = st.session_state.get("passenger_id", "PAX-1042")
    passenger_id = st.text_input("Passenger ID", value=previous_passenger).strip() or "PAX-1042"
    if passenger_id != previous_passenger:
        st.session_state.passenger_id = passenger_id
        st.session_state.messages = []
    else:
        st.session_state.passenger_id = passenger_id

    with st.expander("Teach the agent", expanded=True):
        with st.form("teach_agent"):
            passenger_name = st.text_input("Passenger name", value="Aarav")
            pnr = st.text_input("PNR", value="DEMO-7842")
            train_number = st.text_input("Train number", value="12123")
            origin = st.text_input("From", value="Pune Junction")
            destination = st.text_input("To", value="Mumbai CSMT")
            travel_date = st.text_input("Travel date", value=date.today().isoformat())
            support_need = st.text_input("Assistance or preference", value="Wheelchair assistance")
            issue_description = st.text_area(
                "What happened?",
                value="My train is delayed and I need help with the disruption.",
                height=90,
            )
            submitted = st.form_submit_button("Save journey memory", type="primary", width="stretch")
    if submitted:
        if not all((passenger_name.strip(), pnr.strip(), train_number.strip(), origin.strip(), destination.strip(), issue_description.strip())):
            st.error("Fill in the passenger, trip, and issue fields first.")
        else:
            issue_id = store.teach(
                passenger_id,
                passenger_name.strip(),
                pnr.strip(),
                train_number.strip(),
                origin.strip(),
                destination.strip(),
                travel_date.strip(),
                issue_description.strip(),
                support_need.strip(),
            )
            st.session_state.active_issue_id = issue_id
            st.session_state.messages = []
            st.toast("Journey and support issue saved to memory.")
            st.rerun()

    st.divider()
    st.caption("Demo journeys are fictional. Live running status and ticket eligibility are not queried.")

st.markdown(
    "<div class='rail-header'><div><div class='rail-brand'>RailAssist</div>"
    "<div class='rail-tagline'>A memory-aware passenger support desk</div></div>"
    "<div class='rail-chip'>RAILWAY CUSTOMER CARE</div></div>",
    unsafe_allow_html=True,
)
st.markdown("<h1 class='page-title'>Journey support desk</h1>", unsafe_allow_html=True)
st.markdown(
    "<p class='page-subtitle'>A returning passenger should not have to explain the same disruption twice.</p>",
    unsafe_allow_html=True,
)

context = store.get_context(passenger_id)
trip = context.get("trip")
issue = context.get("issue")
metric_columns = st.columns(3)
metric_columns[0].metric("Journey", f"Train {trip['train_number']}" if trip else "Not taught yet")
metric_columns[1].metric("Open support case", issue["category"].title() if issue else "None")
metric_columns[2].metric("Remembered needs", "Saved" if context.get("support_need") else "None")

support_tab, memory_tab = st.tabs(("Support conversation", "Memory graph"))
with support_tab:
    left, right = st.columns((1.55, 1), gap="large")
    with left:
        st.subheader("Ask about this journey")
        if not context.get("passenger"):
            st.info("Start by saving a journey and issue under **Teach the agent**.")
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message.get("source"):
                    st.caption(f"Response: {message['source']}")
        question = st.chat_input("Ask a follow-up about the saved journey")
        if question:
            context = store.get_context(passenger_id, question)
            response, response_source = generate_response(
                question,
                context,
                llm_client,
                os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            )
            active_issue = context.get("issue")
            store.record_followup(
                passenger_id,
                active_issue["id"] if active_issue else None,
                question,
                response,
            )
            st.session_state.messages.extend(
                (
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": response, "source": response_source},
                )
            )
            st.rerun()
    with right:
        st.subheader("Retrieved memory")
        if trip and issue:
            st.markdown(f"**{context['passenger']['name']}** · Passenger `{passenger_id}`")
            st.markdown(f"Train **{trip['train_number']}** · PNR `{trip['pnr']}`")
            st.caption(f"{trip['origin']} → {trip['destination']} · {trip['travel_date']}")
            st.markdown(f"**Previous issue:** {issue['description']}")
            if context.get("support_need"):
                st.markdown(f"**Remembered assistance:** {context['support_need']}")
            st.caption(f"Retrieved {len(context.get('interactions', []))} related interaction(s) from persistent memory.")
        else:
            st.caption("No journey memory found for this passenger ID.")
        with st.expander("Support guidance used"):
            for policy in context.get("policies", []):
                st.markdown(f"**{policy['title']}**  \n{policy['guidance']}")

with memory_tab:
    st.subheader("Passenger context as connected data")
    if trip and issue:
        graph_rows = [
            {"From": f"Passenger: {context['passenger']['name']}", "Relationship": "HAS_TRIP", "To": f"Train {trip['train_number']} / PNR {trip['pnr']}"},
            {"From": f"Train {trip['train_number']}", "Relationship": "ORIGIN", "To": trip["origin"]},
            {"From": f"Train {trip['train_number']}", "Relationship": "DESTINATION", "To": trip["destination"]},
            {"From": f"Train {trip['train_number']}", "Relationship": "HAS_ISSUE", "To": f"{issue['category'].title()}: {issue['description']}"},
        ]
        if context.get("support_need"):
            graph_rows.append(
                {"From": f"Passenger: {context['passenger']['name']}", "Relationship": "HAS_SUPPORT_NEED", "To": context["support_need"]}
            )
        for policy in context.get("policies", []):
            if policy["category"] == issue["category"]:
                source, relationship = issue["category"].title() + " issue", "GUIDED_BY"
            elif policy["category"] == "accessibility" and context.get("support_need"):
                source, relationship = context["support_need"], "GUIDED_BY"
            else:
                source, relationship = "Current question", "USES_POLICY"
            graph_rows.append({"From": source, "Relationship": relationship, "To": policy["title"]})
        st.dataframe(graph_rows, hide_index=True, width="stretch")
        st.caption("In Aura mode these are persisted Neo4j nodes and relationships. In local preview, the same journey is retained in a local SQLite database.")
    else:
        st.info("Save a journey to see the passenger → trip → issue → policy memory path.")
