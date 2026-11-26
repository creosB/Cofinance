import unittest
from unittest.mock import MagicMock
from agents.team_lead import get_team_lead

class MockAgent:
    def __init__(self, name, response_content):
        self.name = name
        self.response_content = response_content
        self.run_called = False
        self.last_query = None

    def run(self, query, stream=False):
        self.run_called = True
        self.last_query = query
        mock_response = MagicMock()
        mock_response.content = self.response_content
        return mock_response

class TestTeamLeadDelegation(unittest.TestCase):
    @unittest.mock.patch('agents.team_lead.SqlAgentStorage')
    @unittest.mock.patch('agents.team_lead.Agent')
    def test_delegation_tools(self, mock_agent_cls, mock_storage):
        # Setup mock agents
        data_agent = MockAgent("Data Analyst", "Market data for NVDA")
        news_agent = MockAgent("News Researcher", "News for NVDA")
        
        # Mock model config
        model_config = MagicMock()
        
        # Call get_team_lead
        get_team_lead(model_config, [data_agent, news_agent], "test_session")
        
        # Verify Agent was initialized
        self.assertTrue(mock_agent_cls.called)
        
        # Get the tools passed to Agent
        call_args = mock_agent_cls.call_args
        kwargs = call_args.kwargs
        tools = kwargs.get('tools', [])
        
        # Create a map of tool names to functions
        tools_map = {t.__name__: t for t in tools}
        
        # Test call_data_analyst
        self.assertIn("call_data_analyst", tools_map)
        response_data = tools_map["call_data_analyst"]("Get price for NVDA")
        
        self.assertTrue(data_agent.run_called)
        self.assertEqual(data_agent.last_query, "Get price for NVDA")
        self.assertEqual(response_data, "Market data for NVDA")
        
        # Test call_news_researcher
        self.assertIn("call_news_researcher", tools_map)
        response_news = tools_map["call_news_researcher"]("Get news for NVDA")
        
        self.assertTrue(news_agent.run_called)
        self.assertEqual(news_agent.last_query, "Get news for NVDA")
        self.assertEqual(response_news, "News for NVDA")

    @unittest.mock.patch('agents.team_lead.SqlAgentStorage')
    @unittest.mock.patch('agents.team_lead.Agent')
    def test_thinking_mode_instructions_include_think_tag(self, mock_agent_cls, mock_storage):
        model_config = MagicMock()
        data_agent = MagicMock()
        news_agent = MagicMock()

        # Call get_team_lead with thinking_mode True
        get_team_lead(model_config, [data_agent, news_agent], "test_session", thinking_mode=True)

        # Ensure Agent was instantiated with instructions including the <think> instruction
        self.assertTrue(mock_agent_cls.called)
        kwargs = mock_agent_cls.call_args.kwargs
        instructions = kwargs.get('instructions', [])
        found_think_instr = any('<think>' in (s.lower() if isinstance(s, str) else '') for s in instructions)
        # The instruction we added asks the agent to wrap the Strategic Analysis in <think> tags
        self.assertTrue(found_think_instr)

if __name__ == "__main__":
    unittest.main()
