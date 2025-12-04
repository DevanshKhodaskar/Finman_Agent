# send_image.py

from langchain_bot import create_graph, encode_image_to_data_url, HumanMessage

def send_image_message(
    image_path: str,
    prompt_text: str = "Describe this image."
):
    """Send a single image + prompt to the langchain graph and print the reply."""
    # 1) Build graph
    app = create_graph()

    # 2) Start empty conversation
    conversation_state = {"messages": []}

    # 3) Encode local image to data URL (same helper as your bot)
    data_url = encode_image_to_data_url(image_path)

    # 4) Build HumanMessage: text + image_url
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

    # 5) Add to state
    conversation_state["messages"].append(human_msg)

    # 6) Invoke graph
    result = app.invoke(conversation_state)

    # 7) Get last AI reply
    last_message = result["messages"][-1]
    print("🟢 Bot response:\n")
    print(last_message.content)


if __name__ == "__main__":
    # Example usage with lays.jpeg
    send_image_message(
        image_path="lays.jpeg",
        prompt_text="Describe."
    )
