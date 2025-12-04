# langchain_bot.py

import os
import base64
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

# Initialize LLM (same model you used in temp.py)
llm = ChatGroq(
    model="meta-llama/llama-4-scout-17b-16e-instruct",
    temperature=0.7,
    api_key=GROQ_API_KEY,
)

# Define conversation state
class ChatState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]


def encode_image_to_data_url(path: str) -> str:
    """Read local image and return data URL string."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"❌ Image not found at: {path}")

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    # Assuming JPEG; change to image/png if needed
    return f"data:image/jpeg;base64,{b64}"


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
    conversation_state: ChatState = {"messages": []}

    print("🤖 Chatbot ready! Type 'quit' to exit.")
    print("💡 To send an image from the current folder, use:")
    print("   image <filename> <your prompt>")
    print("   e.g. image lays.jpeg Describe this chips packet.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ["quit", "exit", "bye"]:
            print("Goodbye!")
            break
        if not user_input:
            continue

        # --- Image command handler ---
        # Supported formats:
        #   image lays.jpeg what is on this packet?
        #   img lays.jpeg describe it
        tokens = user_input.split(maxsplit=2)
        is_image_cmd = (
            len(tokens) >= 2 and tokens[0].lower() in ("image", "img")
        )

        if is_image_cmd:
            filename = tokens[1]
            prompt_text = (
                tokens[2] if len(tokens) >= 3 else "Describe this image."
            )

            try:
                data_url = encode_image_to_data_url(filename)

                # Multimodal HumanMessage: text + image_url (data URL)
                human_msg = HumanMessage(
                    content=[
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                            },
                        },
                    ]
                )
            except Exception as e:
                print(f"❌ Error loading image: {e}\n")
                continue
        else:
            # Normal text-only message
            human_msg = HumanMessage(content=user_input)

        # Add user message to state
        conversation_state["messages"] = [
            *conversation_state["messages"],
            human_msg,
        ]

        # Invoke graph
        result = app.invoke(conversation_state)
        conversation_state = result

        last_message = conversation_state["messages"][-1]
        print(f"Bot: {last_message.content}\n")


if __name__ == "__main__":
    run_chat()
