"""
Activity Tracker for Agent Tool Calls and Delegations

Automatically tracks and formats:
- Which tools are called by which agents
- Which agents delegate to other agents
- User-friendly display in the reasoning section
"""

import streamlit as st
from typing import Dict, List, Optional, Tuple
from datetime import datetime


# Tool-to-Agent Registry
# Maps tool names to their owning agents
TOOL_AGENT_REGISTRY = {
    # Data Analyst tools
    'get_market_data': 'Data Analyst',
    'plot_stock_history': 'Data Analyst',
    'get_fundamental_metrics': 'Data Analyst',
    'get_analyst_recommendations': 'Data Analyst',
    'compare_stocks': 'Data Analyst',
    'get_watchlist_summary': 'Data Analyst',
    
    # News Researcher tools
    'get_company_news': 'News Researcher',
    'get_watchlist_news': 'News Researcher',
    
    # Team Lead tools
    'add_to_watchlist': 'Team Lead',
    'call_data_analyst': 'Team Lead',
    'call_news_researcher': 'Team Lead',
}

# Agent emoji mapping for display
AGENT_EMOJI = {
    'Data Analyst': '📊',
    'News Researcher': '📰',
    'Team Lead': '👔',
    'System': '🔧',
}


class ActivityTracker:
    """Tracks tool calls and agent delegations for display in reasoning section."""
    
    def __init__(self, session_id: str):
        """
        Initialize the activity tracker.
        
        Args:
            session_id: Current session ID for scoping activities
        """
        self.session_id = session_id
        
        # Initialize session state storage if not exists
        if 'activity_log' not in st.session_state:
            st.session_state.activity_log = []
        
        if 'agent_delegations' not in st.session_state:
            st.session_state.agent_delegations = []
    
    def log_tool_call(self, tool_name: str, args: Dict, agent_name: Optional[str] = None) -> None:
        """
        Log a tool call with automatic agent identification.
        
        Args:
            tool_name: Name of the tool being called
            args: Arguments passed to the tool
            agent_name: Optional explicit agent name (if None, uses registry)
        """
        # Determine agent if not explicitly provided
        if agent_name is None:
            agent_name = TOOL_AGENT_REGISTRY.get(tool_name, 'System')
        
        # Create activity entry
        activity = {
            'type': 'tool_call',
            'tool_name': tool_name,
            'agent': agent_name,
            'args': args,
            'timestamp': datetime.now().isoformat()
        }
        
        # Store in session state
        st.session_state.activity_log.append(activity)
    
    def log_agent_delegation(self, from_agent: str, to_agent: str, query: str) -> None:
        """
        Log when one agent delegates work to another.
        
        Args:
            from_agent: Name of the agent delegating
            to_agent: Name of the agent receiving the delegation
            query: Query/task being delegated
        """
        delegation = {
            'type': 'delegation',
            'from_agent': from_agent,
            'to_agent': to_agent,
            'query': query,
            'timestamp': datetime.now().isoformat()
        }
        
        st.session_state.agent_delegations.append(delegation)
    
    def get_formatted_activities(self) -> str:
        """
        Get a user-friendly formatted string of all activities.
        
        Returns:
            Markdown-formatted string showing tools called and agents delegated
        """
        output = []
        
        # Section 1: Agent Delegations
        if st.session_state.agent_delegations:
            output.append("### 🤝 Agent Collaboration\n")
            for delegation in st.session_state.agent_delegations:
                from_emoji = AGENT_EMOJI.get(delegation['from_agent'], '🔧')
                to_emoji = AGENT_EMOJI.get(delegation['to_agent'], '🔧')
                output.append(
                    f"{from_emoji} **{delegation['from_agent']}** → "
                    f"{to_emoji} **{delegation['to_agent']}**\n"
                )
                # Show truncated query
                query_preview = delegation['query'][:60] + "..." if len(delegation['query']) > 60 else delegation['query']
                output.append(f"   └─ _\"{query_preview}\"_\n")
            output.append("\n")
        
        # Section 2: Tool Calls by Agent
        if st.session_state.activity_log:
            output.append("### 🔧 Tools Called\n")
            
            # Group tools by agent
            tools_by_agent: Dict[str, List[Tuple[str, Dict]]] = {}
            for activity in st.session_state.activity_log:
                agent = activity['agent']
                if agent not in tools_by_agent:
                    tools_by_agent[agent] = []
                tools_by_agent[agent].append((activity['tool_name'], activity['args']))
            
            # Display grouped by agent
            for agent, tools in tools_by_agent.items():
                emoji = AGENT_EMOJI.get(agent, '🔧')
                output.append(f"\n{emoji} **{agent}:**\n")
                for tool_name, args in tools:
                    output.append(f"   • `{tool_name}()`")
                    if args:
                        # Format args nicely (limit to prevent overflow)
                        args_str = ", ".join([f"{k}='{v}'" for k, v in list(args.items())[:3]])
                        if len(args) > 3:
                            args_str += ", ..."
                        output.append(f" — {args_str}")
                    output.append("\n")
        
        return "".join(output)
    
    def get_agents_active(self) -> List[str]:
        """
        Get list of unique agents that were active in this session.
        
        Returns:
            List of agent names
        """
        agents = set()
        
        # From delegations
        for delegation in st.session_state.agent_delegations:
            agents.add(delegation['from_agent'])
            agents.add(delegation['to_agent'])
        
        # From tool calls
        for activity in st.session_state.activity_log:
            agents.add(activity['agent'])
        
        return sorted(list(agents))
    
    def clear(self) -> None:
        """Clear all tracked activities for the current session."""
        st.session_state.activity_log = []
        st.session_state.agent_delegations = []
    
    @staticmethod
    def get_agent_for_tool(tool_name: str) -> str:
        """
        Get the agent name for a given tool.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            Agent name (or 'System' if not found)
        """
        return TOOL_AGENT_REGISTRY.get(tool_name, 'System')
    
    @staticmethod
    def get_emoji_for_agent(agent_name: str) -> str:
        """
        Get the emoji for a given agent.
        
        Args:
            agent_name: Name of the agent
            
        Returns:
            Emoji string
        """
        return AGENT_EMOJI.get(agent_name, '🔧')
