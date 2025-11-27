# langchain_bot.py

import os
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from typing import TypedDict, Annotated, Sequence
import operator

# Load environment variables
load_dotenv()

# Fetch API key from .env file
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise ValueError("❌ GROQ_API_KEY not found in .env file!")

# Initialize LLM
llm = ChatGroq(model="meta-llama/llama-4-scout-17b-16e-instruct", temperature=0.7, api_key=GROQ_API_KEY)

# Define conversation state
class ChatState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]

def create_graph():
    """Create and compile the chat graph"""
    graph = StateGraph(ChatState)

    def chat_node(state: ChatState):
        """Process messages and generate response"""
        try:
            messages = state["messages"]
            response = llm.invoke(messages)
            return {"messages": [response]}
        except Exception as e:
            error_msg = AIMessage(content=f"Error: {str(e)}")
            return {"messages": [error_msg]}

    # Add node and edges
    graph.add_node("chat_node", chat_node)
    graph.add_edge(START, "chat_node")
    graph.add_edge("chat_node", END)

    return graph.compile()

def run_chat():
    """Run interactive chat session"""
    app = create_graph()
    conversation_state = {"messages": []}
    
    print("🤖 Chatbot ready! Type 'quit' to exit.\n")
    
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ['quit', 'exit', 'bye']:
            print("Goodbye!")
            break
        if not user_input:
            continue
        
        conversation_state["messages"].append(HumanMessage(content=user_input))
        result = app.invoke(conversation_state)
        conversation_state = result
        last_message = conversation_state["messages"][-1]
        print(f"Bot: {last_message.content}\n")

if __name__ == "__main__":
    run_chat()
