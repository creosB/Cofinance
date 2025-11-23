import streamlit as st
from datetime import datetime
import uuid
import json
import pandas as pd
import plotly.io as pio

from utils.db import get_all_sessions, get_watchlist, clear_watchlist, remove_from_watchlist
from utils.report_generator import generate_report
from utils.memory import MemoryStore
from utils.code_utils import extract_code_blocks
from tools.code_exec import execute_python
from utils.vector_store import get_vector_env_status
from utils.llm_utils import fetch_llm_studio_models

def render_sidebar():
    """Renders the sidebar with provider settings, memory, and controls."""
    with st.sidebar:
        st.markdown("### ⚙️ LLM Provider")
        if 'llm_provider' not in st.session_state:
            st.session_state.llm_provider = "LLM Studio"
        provider = st.selectbox(
            "Select Provider:",
            ["LLM Studio", "Gemini", "OpenRouter"],
            index=["LLM Studio", "Gemini", "OpenRouter"].index(st.session_state.llm_provider),
            key="provider_select",
        )
        st.session_state.llm_provider = provider
        api_key = None
        if provider == "Gemini":
            import os
            api_key = os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                api_key = st.text_input("Google API Key", type="password")
                if api_key:
                    os.environ["GOOGLE_API_KEY"] = api_key
            
            # Gemini model selector
            gemini_models = [
                "gemini-flash-latest",
                "gemini-3-pro-preview",
                "gemini-2.5-pro",
                "gemini-flash-lite-latest"
            ]
            
            # Initialize default model if not set
            if 'gemini_model' not in st.session_state:
                st.session_state.gemini_model = "gemini-2.0-flash-exp"
            
            # Model selector dropdown
            selected_model = st.selectbox(
                "Select Model:",
                gemini_models,
                index=gemini_models.index(st.session_state.gemini_model) if st.session_state.gemini_model in gemini_models else 0,
                key="gemini_model_select",
                help="Choose a Gemini model"
            )
            st.session_state.gemini_model = selected_model
            st.caption(f"🔮 Gemini: {selected_model}")
        elif provider == "OpenRouter":
            import os
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                api_key = st.text_input("OpenRouter API Key", type="password")
                if api_key:
                    os.environ["OPENROUTER_API_KEY"] = api_key
            st.caption("Model: deepseek/deepseek-r1:free")
        else:
            # LLM Studio - Add model selector
            available_models = fetch_llm_studio_models()
            
            # Initialize default model if not set
            if 'llm_studio_model' not in st.session_state:
                st.session_state.llm_studio_model = available_models[0] if available_models else "qwen/qwen3-4b-2507"
            
            # Model selector dropdown
            selected_model = st.selectbox(
                "Select Model:",
                available_models,
                index=available_models.index(st.session_state.llm_studio_model) if st.session_state.llm_studio_model in available_models else 0,
                key="llm_studio_model_select",
                help="Models from LLM Studio (http://127.0.0.1:1234)"
            )
            st.session_state.llm_studio_model = selected_model
            st.caption(f"🖥️ Local: {selected_model}")
            api_key = "local"

        # Thinking Mode Toggle
        st.markdown("### 🤔 Thinking Mode")
        thinking_mode = st.checkbox(
            "Enable Deep Thinking",
            value=st.session_state.get('thinking_mode', False),
            key="thinking_mode_toggle",
            help="When enabled, the AI will think step-by-step before responding, providing more detailed analysis."
        )
        st.session_state.thinking_mode = thinking_mode

        st.divider()
        st.markdown("### 🗂️ Chat History")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("➕ New Chat", use_container_width=True):
                st.session_state.session_id = str(uuid.uuid4())
                st.session_state.messages = []
                if 'pending_charts' in st.session_state:
                    del st.session_state['pending_charts']
                st.rerun()
        with col2:
            if st.button("🗑️ Delete All", use_container_width=True):
                from utils.db import delete_all_sessions
                delete_all_sessions()
                st.session_state.session_id = str(uuid.uuid4())
                st.session_state.messages = []
                if 'pending_charts' in st.session_state:
                    del st.session_state['pending_charts']
                st.rerun()
        sessions = get_all_sessions()
        if sessions:
            st.caption("📝 Recent Conversations")
            for sess in sessions:
                sid = sess[0]
                # Get first user message for label
                try:
                    mem = MemoryStore()
                    msgs = mem.get_messages(sid, limit=1)
                    if msgs and len(msgs) > 0:
                        first_msg = msgs[0][2]  # message content
                        label = f"{first_msg[:28]}..." if len(first_msg) > 28 else f"{first_msg}"
                    else:
                        created = sess[1] if len(sess) > 1 else None
                        if created:
                            dt = datetime.fromisoformat(created)
                            label = f"{dt.strftime('%b %d, %H:%M')}"
                        else:
                            label = "New conversation"
                except Exception:
                    label = "Conversation"
                
                # Modern layout with load and delete buttons
                col1, col2 = st.columns([4, 1])
                with col1:
                    if st.button(f"💬 {label}", key=f"sess_{sid}", use_container_width=True):
                        st.session_state.session_id = sid
                        st.session_state.messages = []
                        if 'pending_charts' in st.session_state:
                            del st.session_state['pending_charts']
                        st.rerun()
                with col2:
                    if st.button("🗑️", key=f"del_{sid}", help="Delete chat", use_container_width=True):
                        from utils.db import delete_session
                        delete_session(sid)
                        # If deleting current session, create a new one
                        if st.session_state.get('session_id') == sid:
                            st.session_state.session_id = str(uuid.uuid4())
                            st.session_state.messages = []
                            if 'pending_charts' in st.session_state:
                                del st.session_state['pending_charts']
                        st.rerun()

        st.divider()
        st.markdown("### 👀 Watchlist")
        watchlist_data = get_watchlist()
        if watchlist_data:
            st.caption(f"📋 {len(watchlist_data)} stocks tracked")
            
            # Build detailed watchlist table
            import yfinance as yf
            
            watchlist_rows = []
            for ticker, added_at in watchlist_data:
                try:
                    stock = yf.Ticker(ticker)
                    info = stock.info
                    
                    # Get name
                    name = info.get('shortName', ticker)[:20]  # Truncate for display
                    
                    # Get current price
                    price = info.get('currentPrice', info.get('regularMarketPrice', 0))
                    if price:
                        price_str = f"${price:.2f}"
                    else:
                        price_str = "N/A"
                    
                    # Calculate recommendation based on 52-week range
                    low_52w = info.get('fiftyTwoWeekLow', price)
                    high_52w = info.get('fiftyTwoWeekHigh', price)
                    
                    if price and low_52w and high_52w and high_52w != low_52w:
                        position = (price - low_52w) / (high_52w - low_52w)
                        if position < 0.3:
                            rec = "🟢 BUY"
                            rec_color = "#10A37F"
                        elif position > 0.7:
                            rec = "🔴 SELL"
                            rec_color = "#EF4444"
                        else:
                            rec = "🟡 HOLD"
                            rec_color = "#FBBF24"
                    else:
                        rec = "⚪ HOLD"
                        rec_color = "#9CA3AF"
                    
                    # Format date
                    try:
                        dt = datetime.fromisoformat(added_at)
                        date_str = dt.strftime('%b %d')
                    except:
                        date_str = "N/A"
                    
                    watchlist_rows.append({
                        'ticker': ticker,
                        'name': name,
                        'price': price_str,
                        'rec': rec,
                        'rec_color': rec_color,
                        'date': date_str
                    })
                except Exception:
                    # Fallback for failed fetches
                    watchlist_rows.append({
                        'ticker': ticker,
                        'name': ticker,
                        'price': 'N/A',
                        'rec': '⚪ HOLD',
                        'rec_color': '#9CA3AF',
                        'date': 'N/A'
                    })
            
            # Display watchlist items with individual delete buttons
            for row in watchlist_rows:
                # Create columns for content and delete button
                col_main, col_del = st.columns([5, 1])
                
                with col_main:
                    st.markdown(f"""
                    <div style="
                        padding: 10px;
                        background: rgba(16, 163, 127, 0.05);
                        border-left: 3px solid {row['rec_color']};
                        border-radius: 6px;
                        margin-bottom: 8px;
                    ">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div style="flex: 1;">
                                <strong style="color: #10A37F;">{row['ticker']}</strong><br>
                                <span style="font-size: 0.85em; color: #ECECEC;">{row['name']}</span>
                            </div>
                            <div style="text-align: right;">
                                <div style="font-weight: bold;">{row['price']}</div>
                                <div style="font-size: 0.85em; color: {row['rec_color']};">{row['rec']}</div>
                            </div>
                        </div>
                        <div style="font-size: 0.75em; color: #9CA3AF; margin-top: 4px;">
                            Added: {row['date']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col_del:
                    # Individual delete button with modern styling
                    if st.button("🗑️", key=f"del_watch_{row['ticker']}", help=f"Remove {row['ticker']}", use_container_width=True):
                        result = remove_from_watchlist(row['ticker'])
                        st.toast(result, icon="✅" if "✅" in result else "ℹ️")
                        st.rerun()
            
            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            col1, col2 = st.columns(2)
            with col1:
                if st.button("📊 Analyze All", use_container_width=True, help="Ask agent to analyze your watchlist"):
                    st.session_state['watchlist_query'] = "Analyze my watchlist"
                    st.rerun()
            with col2:
                if st.button("🗑️ Clear All", use_container_width=True, help="Remove all stocks from watchlist"):
                    clear_watchlist()
                    st.toast("Watchlist cleared!", icon="✅")
                    st.rerun()
        else:
            st.info("No stocks tracked yet. Analyze a stock to add it!")

        st.divider()
        if st.session_state.get('messages'):
            st.markdown("### 📤 Export Analysis")
            
            # Generate reports
            report_text = generate_report(st.session_state.messages)
            
            # Export options in columns
            col1, col2 = st.columns(2)
            
            with col1:
                st.download_button(
                    "📄 Markdown",
                    data=report_text,
                    file_name=f"analysis_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
                    mime="text/markdown",
                    width='stretch',
                    help="Download as Markdown document"
                )
            
            with col2:
                # JSON export
                json_data = json.dumps({
                    'session_id': st.session_state.get('session_id'),
                    'timestamp': datetime.now().isoformat(),
                    'provider': st.session_state.get('llm_provider', 'Unknown'),
                    'messages': [
                        {
                            'role': msg.get('role'),
                            'content': msg.get('content'),
                            'has_chart': bool(msg.get('chart'))
                        }
                        for msg in st.session_state.messages
                    ]
                }, indent=2)
                
                st.download_button(
                    "📊 JSON",
                    data=json_data,
                    file_name=f"analysis_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                    mime="application/json",
                    width='stretch',
                    help="Download as JSON (structured data)"
                )
        return api_key

def render_chat_history():
    """Render chat messages, charts, pin + code execution controls."""
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    for idx, message in enumerate(st.session_state.messages):
        with st.chat_message(message['role']):
            st.markdown(message.get('content', ''))

            chart_data = message.get('chart')
            if chart_data:
                if isinstance(chart_data, list):
                    for c_i, chart in enumerate(chart_data):
                        st.plotly_chart(chart, width='stretch', key=f'chart_{idx}_{c_i}')
                else:
                    st.plotly_chart(chart_data, width='stretch', key=f'chart_{idx}')

            if message['role'] == 'assistant':
                sid_pin = st.session_state.get('session_id')
                if sid_pin and st.button('📌 Pin Insight', key=f'pin_{idx}'):
                    try:
                        mem = MemoryStore()
                        snippet = (message.get('content', '')[:400]).strip()
                        mem.add_fact(sid_pin, 'pinned', snippet, score=1.0)
                        mem.log_event(sid_pin, 'PIN_FACT', {'length': len(snippet)})
                        st.success('Pinned to memory.')
                    except Exception:
                        st.error('Failed to pin.')

                blocks = extract_code_blocks(message.get('content', ''), allowed_langs=['python', 'py'])
                for b_i, (_, code) in enumerate(blocks):
                    st.caption(f'Runnable code block #{b_i+1}')
                    st.code(code, language='python')
                    run_key = f'run_{idx}_{b_i}'
                    if st.button('▶️ Run Code', key=run_key):
                        with st.spinner('Executing code…'):
                            res = execute_python(code)
                        if not res.get('ok'):
                            st.error(res.get('error') or res.get('stderr') or 'Execution failed')
                        else:
                            out = res.get('stdout')
                            err = res.get('stderr')
                            if out:
                                st.subheader('Stdout')
                                st.text(out)
                            if err:
                                st.subheader('Stderr')
                                st.text(err)
                            figs = res.get('figures', [])
                            for f_i, fj in enumerate(figs):
                                try:
                                    fig = pio.from_json(fj)
                                    st.plotly_chart(fig, width='stretch', key=f'execfig_{idx}_{b_i}_{f_i}')
                                except Exception:
                                    pass
                            try:
                                mem = MemoryStore()
                                sid = st.session_state.get('session_id')
                                if sid:
                                    if out:
                                        mem.save_artifact(sid, kind='stdout', content=out)
                                    if err:
                                        mem.save_artifact(sid, kind='stderr', content=err)
                                    for fj in figs:
                                        mem.save_artifact(sid, kind='plotly_fig', content=fj)
                                    import hashlib
                                    code_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()[:16]
                                    mem.log_event(sid, 'CODE_EXEC', {'code_hash': code_hash, 'ok': True, 'stdout_len': len(out or ''), 'stderr_len': len(err or ''), 'figs': len(figs)})
                            except Exception:
                                pass
