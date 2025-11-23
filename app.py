import streamlit as st
import os
import uuid
from phi.model.google import Gemini
from phi.model.openai import OpenAIChat

# Import modules
from ui.styles import CUSTOM_CSS
from utils.response_processor import extract_thinking_blocks, add_unique_thought, normalize_thought
from ui.layout import render_sidebar, render_chat_history
from utils.db import init_db, AGENT_DB
from agents.data_agent import get_data_agent
from agents.news_agent import get_news_agent
from agents.team_lead import get_team_lead
from phi.storage.agent.sqlite import SqlAgentStorage
from agents.orchestrator import Orchestrator
from utils.memory import MemoryStore, extract_entities_from_text, compact_session_history
from utils.vector_store import Retriever
from utils.db import get_all_sessions
from utils.activity_tracker import ActivityTracker
import re

# -----------------------------------------------------------------------------
# 1. CONFIG & STYLES
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Cofinance Analyst",
    page_icon="screenshots/Favicons/favicon-32x32.png",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# Initialize DBs
init_db()
mem_store = MemoryStore()

# -----------------------------------------------------------------------------
# 2. MODEL CONFIGURATION
# -----------------------------------------------------------------------------
def get_model_config(provider: str):
    """Returns the appropriate model configuration based on selected provider."""
    if provider == "Gemini":
        # Get selected model from session state, fallback to default
        model_id = st.session_state.get('gemini_model', 'gemini-2.0-flash-exp')
        return Gemini(id=model_id)
    
    elif provider == "OpenRouter":
        return OpenAIChat(
            id="deepseek/deepseek-r1:free",
            api_key=os.environ.get("OPENROUTER_API_KEY", "dummy"),
            base_url="https://openrouter.ai/api/v1"
        )
    
    elif provider == "LLM Studio":
        # Get selected model from session state, fallback to default
        model_id = st.session_state.get('llm_studio_model', 'qwen/qwen3-4b-2507')
        return OpenAIChat(
            id=model_id,
            api_key="not-needed",
            base_url="http://127.0.0.1:1234/v1"
        )
    
    # Fallback to LLM Studio with default model
    model_id = st.session_state.get('llm_studio_model', 'qwen/qwen3-4b-2507')
    return OpenAIChat(
        id=model_id,
        api_key="not-needed",
        base_url="http://127.0.0.1:1234/v1"
    )

# -----------------------------------------------------------------------------
# 3. MAIN APP LOGIC
# -----------------------------------------------------------------------------
def main():
    st.title("Cofinance Analyst")
    st.caption("AI-Powered Investment Research Platform")

    # Render Sidebar and get API Key
    api_key = render_sidebar()

    # Initialize Session ID
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    # Initialize Storage for history loading
    storage = SqlAgentStorage(table_name="agent_sessions", db_url=f"sqlite:///{AGENT_DB}")

    # Load history if needed (logic moved here or kept in main for simplicity)
    if not st.session_state.get("messages") and api_key:
        try:
            session_data = storage.read(session_id=st.session_state.session_id)
            if session_data and hasattr(session_data, 'memory') and session_data.memory:
                if 'messages' in session_data.memory:
                    hist_msgs = session_data.memory['messages']
                    print(f"\n[DEBUG] Loading {len(hist_msgs)} messages from storage")
                    print(f"[DEBUG] Provider: {st.session_state.get('llm_provider', 'Unknown')}")
                    for idx, msg in enumerate(hist_msgs):
                        print(f"[DEBUG] Message {idx}: type={type(msg)}, msg={msg}")
                        # Handle both dict and object formats
                        if isinstance(msg, dict):
                            role = msg.get('role')
                            content = msg.get('content')
                        else:
                            role = getattr(msg, 'role', None)
                            content = getattr(msg, 'content', None)
                        
                        print(f"[DEBUG] Extracted - role={role}, content_len={len(content) if content else 0}")
                        if role in ["user", "assistant"] and content:
                            st.session_state.messages.append({
                                "role": role, 
                                "content": content,
                                "chart": None
                            })
                    print(f"[DEBUG] Loaded {len(st.session_state.messages)} messages into session state")
        except Exception as e:
            print(f"[DEBUG] Exception loading messages: {e}")
            pass

    # Render Chat History (Chat mode)
    render_chat_history()

    # Show welcome message if no messages yet
    if not st.session_state.get('messages'):
        st.markdown("""
        <div style="text-align: center; padding: 40px 20px; border-radius: 10px; background: rgba(16, 163, 127, 0.1); margin: 20px 0;">
            <h2 style="color: #10A37F; margin-bottom: 20px;">👋 Welcome to Cofinance Analyst!</h2>
            <p style="color: #ECECEC; font-size: 1.1em; margin-bottom: 30px;">
                Your AI-powered investment research platform. Hybrid analysis for <strong>Stocks</strong> and <strong>Crypto</strong>.
            </p>
            <div style="display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin-top: 20px;">
                <button style="padding: 10px 20px; background: rgba(16, 163, 127, 0.2); border: 1px solid #10A37F; border-radius: 20px; color: #ECECEC; cursor: pointer;">
                    💡 "Analyze Tesla"
                </button>
                <button style="padding: 10px 20px; background: rgba(16, 163, 127, 0.2); border: 1px solid #10A37F; border-radius: 20px; color: #ECECEC; cursor: pointer;">
                    📊 "Compare NVDA and AMD"
                </button>
                <button style="padding: 10px 20px; background: rgba(251, 191, 36, 0.2); border: 1px solid #FBBF24; border-radius: 20px; color: #ECECEC; cursor: pointer;">
                    🪙 "Analyze Bitcoin"
                </button>
                <button style="padding: 10px 20px; background: rgba(251, 191, 36, 0.2); border: 1px solid #FBBF24; border-radius: 20px; color: #ECECEC; cursor: pointer;">
                    ⚡ "Should I buy Ethereum?"
                </button>
                <button style="padding: 10px 20px; background: rgba(16, 163, 127, 0.2); border: 1px solid #10A37F; border-radius: 20px; color: #ECECEC; cursor: pointer;">
                    📰 "Show news for my watchlist"
                </button>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # Check if "Analyze All" was clicked from watchlist
    if st.session_state.get('watchlist_query'):
        prompt = st.session_state['watchlist_query']
        del st.session_state['watchlist_query']
        # Process this as if user typed it
        if api_key:
            st.session_state.messages.append({"role": "user", "content": prompt, "chart": None})
            st.rerun()

    # Chat Input
    if prompt := st.chat_input("💬 Ask about any stock (e.g., 'Analyze NVDA' or 'Compare TSLA and F')"):
        if not api_key:
            st.error("⚠️ Please provide a valid API Key (or use LLM Studio) to proceed.")
        else:
            # Add user message
            st.session_state.messages.append({"role": "user", "content": prompt, "chart": None})
            # Persist to deep memory
            try:
                mem_store.save_message(st.session_state.session_id, "user", prompt)
                ents = extract_entities_from_text(prompt)
                for t in ents.get("tickers", []):
                    mem_store.add_entity(st.session_state.session_id, "ticker", t)
                for intent in ents.get("intents", []):
                    mem_store.add_fact(st.session_state.session_id, "intent", intent, score=0.9)
            except Exception:
                pass
            with st.chat_message("user"):
                st.markdown(prompt)
            
            # Generate assistant response
            with st.chat_message("assistant"):
                with st.expander("🤖 AI Reasoning & Agent Activity", expanded=True):
                    reasoning_placeholder = st.empty()
                    reasoning_text = "### 🧠 Agent Collaboration\n\n⏳ **Initializing agent team...**\n\n"
                    reasoning_placeholder.markdown(reasoning_text)
                
                response_placeholder = st.empty()
                full_response_visible = ""
                
                try:
                    # Initialize Agent Team via orchestrator (A2A-aware)
                    model_config = get_model_config(st.session_state.llm_provider)
                    thinking_mode = st.session_state.get('thinking_mode', False)
                    orch = Orchestrator(model_config, st.session_state.session_id, thinking_mode=thinking_mode)
                    agent_team = orch.team()

                    reasoning_text += "✅ **Agent team ready**\n\n"
                    reasoning_placeholder.markdown(reasoning_text)

                    # Initialize pending charts list
                    if 'pending_charts' not in st.session_state:
                        st.session_state['pending_charts'] = []
                    
                    # Initialize activity tracker for this session
                    tracker = ActivityTracker(st.session_state.session_id)
                    tracker.clear()  # Clear previous activities for fresh start
                    
                    # Initialize delegated tool calls list (for displaying inner agent tool calls)
                    st.session_state['delegated_tool_calls'] = []
                    
                    # Build retrieval context (cross-session) - LIMIT TO PREVENT OVERFLOW
                    try:
                        reasoning_text += "🔍 **Searching memory and context...**\n\n"
                        reasoning_placeholder.markdown(reasoning_text)
                        
                        retriever = Retriever()
                        current_sid = st.session_state.session_id
                        msgs_current = mem_store.get_messages(current_sid)
                        facts_current = mem_store.get_facts(current_sid, limit=100)  # Reduced from 300
                        msg_texts = [m[2] for m in msgs_current][-50:]  # Reduced from 250
                        fact_texts = []
                        for f in facts_current:
                            line = f"{f[2]}: {f[3]}"
                            fact_texts.append(line)
                            # Duplicate pinned facts to weight them higher
                            if f[2] == 'pinned':
                                fact_texts.append(line)

                        # Cross-session (recent other sessions) - REDUCED
                        other_sessions = [s[0] for s in get_all_sessions() if s[0] != current_sid][:2]  # Reduced from 3
                        cross_texts = []
                        for sid_other in other_sessions:
                            try:
                                msgs_o = mem_store.get_messages(sid_other, limit=20)  # Reduced from 50
                                facts_o = mem_store.get_facts(sid_other, limit=20)  # Reduced from 50
                                cross_texts.extend([m[2] for m in msgs_o])
                                cross_texts.extend([f"{f[2]}: {f[3]}" for f in facts_o])
                            except Exception:
                                pass

                        texts = msg_texts + fact_texts + cross_texts
                        kinds = (["message"] * len(msg_texts) + ["fact"] * len(fact_texts) + ["cross"] * len(cross_texts))
                        retriever.build(texts, kinds)
                        results = retriever.search(prompt, top_k=3)  # Reduced from 5
                        ctx_lines = [f"- ({r.kind}, {r.score:.2f}) {r.text[:100]}" for r in results]  # Truncate text
                        retrieved_context = "\n".join(ctx_lines)
                        
                        if retrieved_context:
                            reasoning_text += f"✅ **Found {len(results)} relevant context items**\n\n"
                        else:
                            reasoning_text += "ℹ️ **No prior context found**\n\n"
                        reasoning_placeholder.markdown(reasoning_text)
                    except Exception:
                        retrieved_context = ""
                    
                    # Compose prompt with retrieved context (if any) - LIMIT TOTAL LENGTH
                    if retrieved_context:
                        # Limit retrieved context to prevent overflow
                        max_context_chars = 500
                        if len(retrieved_context) > max_context_chars:
                            retrieved_context = retrieved_context[:max_context_chars] + "...[truncated]"
                        composed_prompt = f"Relevant Context (retrieved):\n{retrieved_context}\n\n{prompt}"
                    else:
                        composed_prompt = prompt

                    reasoning_text += "---\n\n### 🔄 Agent Activity\n\n"
                    reasoning_text += "_Waiting for agent responses..._\n\n"
                    reasoning_placeholder.markdown(reasoning_text)

                    # Run the agent team
                    response_stream = agent_team.run(composed_prompt, stream=True)
                    
                    # Track which agents have been seen
                    agents_active = set()
                    # Track deduplicated thought content to avoid duplicate 'Thinking' entries
                    existing_thoughts: set = set()
                    
                    # Stream the response, filtering technical messages and showing agent activity
                    accumulated_response = ""
                    last_update_time = 0
                    import time
                    
                    for chunk in response_stream:
                        if chunk.content:
                            text = chunk.content
                            accumulated_response += text

                            # Extract multi-line <think> blocks (case-insensitive, dotall)
                            # Extract any <think> blocks (multi-line) using helper and de-duplicate
                            filtered, think_matches = extract_thinking_blocks(accumulated_response)
                            for match in think_matches:
                                reasoning_text, existing_thoughts = add_unique_thought(reasoning_text, existing_thoughts, match)
                            accumulated_response = filtered

                            # Filter technical patterns from accumulated response
                            lines = accumulated_response.split('\n')
                            filtered_lines = []
                            
                            for line in lines:
                                line_lower = line.strip().lower()
                                should_filter = False
                                
                                # Check if this is a technical line or special token
                                if line_lower.startswith('running:'):
                                    # Skip duplicate "Running:" - it's handled by tool_calls
                                    should_filter = True
                                elif '<|channel|>' in line_lower or '<|' in line and '|>' in line:
                                    # Filter LLM Studio special tokens
                                    should_filter = True
                                elif 'commentary to=' in line_lower or 'function?' in line_lower:
                                    # Filter LLM Studio function call formatting
                                    should_filter = True
                                elif line_lower.startswith('???') or line.strip() == '???':
                                    # Filter uncertainty markers
                                    should_filter = True
                                elif 'call_data_analyst' in line_lower:
                                    # Filter out - will be shown via tool_calls
                                    should_filter = True
                                elif 'call_news_researcher' in line_lower:
                                    # Filter out - will be shown via tool_calls
                                    should_filter = True
                                elif 'transfer_task_to_data_analyst' in line_lower or 'transfer to data_analyst' in line_lower:
                                    # Old delegation pattern (shouldn't happen with new tools)
                                    should_filter = True
                                elif 'transfer_task_to_news' in line_lower or 'transfer to news' in line_lower:
                                    # Old delegation pattern (shouldn't happen with new tools)
                                    should_filter = True
                                elif 'transfer_task_to' in line_lower or 'transferring to' in line_lower or 'calling agent' in line_lower:
                                    # Generic delegation patterns
                                    should_filter = True
                                elif '<think>' in line_lower or '</think>' in line_lower:
                                    # This handles inline <think> tags or partial tags in a single line.
                                    contains_open = '<think>' in line_lower
                                    contains_close = '</think>' in line_lower
                                    # Remove both opening and closing tags
                                    clean = re.sub(r'</?think>', '', line, flags=re.IGNORECASE).strip()
                                    # Only add to reasoning if this line contains a complete <think> block
                                    if clean and contains_open and contains_close:
                                        reasoning_text, existing_thoughts = add_unique_thought(
                                            reasoning_text, existing_thoughts, clean
                                        )
                                    # Hide all <think> lines (partial or complete) from the visible chat
                                    should_filter = True
                                elif line.strip().startswith('💭') or line_lower.startswith('strategy analysis') or line_lower.startswith('strategic analysis'):
                                    # Normalize and dedupe 'user-friendly' thinking lines (beginning with the '💭' emoji or 'Strategy Analysis' label).
                                    # Only hide them from chat if they are duplicates (i.e., already present in reasoning_text).
                                    clean = line.strip()
                                    norm = normalize_thought(clean)
                                    if norm in existing_thoughts:
                                        should_filter = True
                                    else:
                                        reasoning_text, existing_thoughts = add_unique_thought(reasoning_text, existing_thoughts, clean)
                                        should_filter = False
                                
                                if not should_filter:
                                    filtered_lines.append(line)
                            
                            # Reconstruct response without technical lines
                            full_response_visible = '\n'.join(filtered_lines)
                            
                            # Update UI every 0.5 seconds to avoid excessive redraws
                            current_time = time.time()
                            if current_time - last_update_time > 0.5:
                                response_placeholder.markdown(full_response_visible)
                                if reasoning_text.count('\n') % 3 == 0:
                                    reasoning_placeholder.markdown(reasoning_text)
                                last_update_time = current_time
                        
                        # Show tool calls in reasoning + log to memory (A2A)
                        if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                            for tool_call in chunk.tool_calls:
                                tool_name = tool_call.get('name', 'Unknown')
                                tool_args = tool_call.get('arguments', {})
                                
                                # Log to activity tracker (automatic agent identification)
                                tracker.log_tool_call(tool_name, tool_args)
                                
                                # Get agent info from tracker for display
                                agent_name = tracker.get_agent_for_tool(tool_name)
                                agent_emoji = tracker.get_emoji_for_agent(agent_name)
                                agents_active.add(agent_name)
                                
                                reasoning_text += f"\n{agent_emoji} **[{agent_name}]** → `{tool_name}()`\n"
                                if tool_args:
                                    # Format args nicely
                                    args_str = ", ".join([f"{k}='{v}'" for k, v in tool_args.items()])
                                    reasoning_text += f"   └─ 📝 {args_str}\n"
                                reasoning_placeholder.markdown(reasoning_text)
                            orch.log_tool_calls(chunk)
                    
                    # Check for delegated tool calls (from Data Analyst / News Researcher)
                    if 'delegated_tool_calls' in st.session_state and st.session_state['delegated_tool_calls']:
                        for delegated_call in st.session_state['delegated_tool_calls']:
                            agent_name = delegated_call['agent']
                            tool_name = delegated_call['tool']
                            tool_args = delegated_call['args']
                            
                            # Log to activity tracker
                            tracker.log_tool_call(tool_name, tool_args, agent_name)
                            
                            # Get emoji from tracker
                            agent_emoji = tracker.get_emoji_for_agent(agent_name)
                            agents_active.add(agent_name)
                            
                            reasoning_text += f"\n{agent_emoji} **[{agent_name}]** → `{tool_name}()`\n"
                            if tool_args:
                                # Format args nicely
                                args_str = ", ".join([f"{k}='{v}'" for k, v in tool_args.items()])
                                reasoning_text += f"   └─ 📝 {args_str}\n"
                        
                        # Clear delegated tool calls for next run
                        st.session_state['delegated_tool_calls'] = []
                        reasoning_placeholder.markdown(reasoning_text)
                    
                    # Final updates with activity summary
                    reasoning_text += "\n---\n\n"
                    
                    # Add formatted activities from tracker
                    formatted_activities = tracker.get_formatted_activities()
                    if formatted_activities:
                        reasoning_text += formatted_activities + "\n"
                    
                    if agents_active:
                        reasoning_text += f"**Agents Deployed:** {', '.join(sorted(agents_active))}\n\n"
                    reasoning_text += "✅ **Analysis complete!**\n"
                    reasoning_placeholder.markdown(reasoning_text)
                    
                    # Final cleanup: remove any trailing technical lines
                    final_lines = full_response_visible.strip().split('\n')
                    clean_lines = []
                    for line in final_lines:
                        line_lower = line.lower()
                        if line.strip() and not any(skip in line_lower for skip in [
                            'running:', 
                            'transfer_task_to', 
                            '<think>', 
                            '</think>',
                            'call_data_analyst',
                            'call_news_researcher',
                            '<|channel|>',
                            '<|',
                            'commentary to=',
                            'function?',
                            '???'
                        ]) and not (line.strip().startswith('<|') and '|>' in line):
                            clean_lines.append(line)
                    
                    full_response_visible = '\n'.join(clean_lines)
                    
                    # Check if response is empty
                    if not full_response_visible.strip():
                        full_response_visible = "⚠️ The analysis completed but produced no output. This may indicate:\n\n"
                        full_response_visible += "1. The model quota is exhausted\n"
                        full_response_visible += "2. The query was too complex\n"
                        full_response_visible += "3. A configuration issue with the selected LLM provider\n\n"
                        full_response_visible += "Please try:\n"
                        full_response_visible += "- Switching to a different LLM provider in the sidebar\n"
                        full_response_visible += "- Simplifying your query\n"
                        full_response_visible += "- Checking your API key/quota\n"
                    
                    response_placeholder.markdown(full_response_visible)
                    
                    # Collect charts
                    charts_to_attach = st.session_state.get('pending_charts', [])
                    for chart in charts_to_attach:
                        st.plotly_chart(chart, width='stretch')
                    
                    # Save all charts to the message
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": full_response_visible,
                        "chart": charts_to_attach  # List of charts
                    })
                    
                    st.session_state['pending_charts'] = []
                    
                    # Save messages to storage for persistence
                    try:
                        storage = SqlAgentStorage(table_name="agent_sessions", db_url=f"sqlite:///{AGENT_DB}")
                        session_data = storage.read(session_id=st.session_state.session_id)
                        if not session_data:
                            session_data = storage.create(session_id=st.session_state.session_id)
                        
                        # Normalize messages to plain dicts before saving
                        normalized_messages = [
                            {"role": msg["role"], "content": msg["content"]} 
                            for msg in st.session_state.messages
                        ]
                        session_data.memory = {"messages": normalized_messages}
                        print(f"\n[DEBUG] Saving {len(normalized_messages)} messages to storage")
                        print(f"[DEBUG] Provider: {st.session_state.get('llm_provider', 'Unknown')}")
                        print(f"[DEBUG] First message: {normalized_messages[0] if normalized_messages else 'None'}")
                        print(f"[DEBUG] Last message: {normalized_messages[-1] if normalized_messages else 'None'}")
                        storage.upsert(session_data)
                        print(f"[DEBUG] Successfully saved messages")
                    except Exception as e:
                        print(f"[DEBUG] Exception saving messages: {e}")
                        pass
                    # Persist assistant message and extracted entities
                    try:
                        mem_store.save_message(st.session_state.session_id, "assistant", full_response_visible)
                        mem_store.log_event(st.session_state.session_id, "AGENT_MESSAGE", {"role": "assistant"})
                        ents = extract_entities_from_text(full_response_visible)
                        for t in ents.get("tickers", []):
                            mem_store.add_entity(st.session_state.session_id, "ticker", t)
                    except Exception:
                        pass
                    
                    # Compact history to manage context window
                    try:
                        compact_session_history(st.session_state.session_id, model_config)
                    except Exception:
                        pass
                    
                except Exception as e:
                    error_msg = str(e)
                    if "context length" in error_msg.lower() or "overflow" in error_msg.lower():
                        st.error("⚠️ **Analysis Error:** Context too large for current model. Try:\n"
                                "- Using a shorter query\n"
                                "- Starting a new chat session\n"
                                "- Switching to Gemini or OpenRouter (supports larger context)")
                        reasoning_text += "\n\n❌ **Error:** Context overflow - model context limit reached\n"
                        reasoning_placeholder.markdown(reasoning_text)
                    else:
                        st.error(f"❌ Analysis failed: {error_msg}")
                        reasoning_text += f"\n\n❌ **Error:** {error_msg}\n"
                        reasoning_placeholder.markdown(reasoning_text)

if __name__ == "__main__":
    main()
