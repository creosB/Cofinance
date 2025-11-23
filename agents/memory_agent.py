from phi.agent import Agent

def get_memory_agent(model_config):
    return Agent(
        name="Memory Agent",
        role="Conversation Summarizer",
        model=model_config,
        instructions=[
            "You are a memory management agent.",
            "Your goal is to summarize conversation history to save context window space.",
            "You will be given a list of chat messages.",
            "Create a concise summary (max 200 tokens) of key decisions, tickers analyzed, and outcomes.",
            "Preserve the final recommendation if present.",
            "Do not lose important context like specific numbers or tickers.",
        ],
        markdown=True,
    )

def summarize_chat_history(history: list, model_config) -> str:
    agent = get_memory_agent(model_config)
    # Convert history to a string format for the agent
    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])
    prompt = f"Review the following conversation history and create a concise summary:\n\n{history_text}"
    
    response = agent.run(prompt)
    return response.content
