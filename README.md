# Budget Manager Bot 🤖💰
A Telegram bot that helps you track expenses using AI-powered receipt scanning and natural language processing.
## Features ✨
- 📸 **Receipt Scanning**: Upload photos of receipts for automatic expense extraction- 💬 **Natural Language Input**: Simply type expenses like "Coffee ₹50" or "Lunch at restaurant 350"
- 🤖 **AI-Powered Categorization**: Automatically categorizes expenses using LLM- 🔐 **User Authentication**: Secure login and registration system
- 💾 **MongoDB Storage**: Persistent expense tracking with MongoDB- 📊 **Expense Management**: Track and manage your spending history
## Tech Stack 🛠️
- **Python 3.x**
- **python-telegram-bot**: Telegram Bot API wrapper- **LangChain & LangGraph**: AI conversation flow management
- **Groq API**: LLM inference (Llama 4 Scout)
- **MongoDB (Motor)**: Async database operations- **bcrypt**: Password hashing and security
## Setup 🚀
### Prerequisites
- Python 3.8+- MongoDB instance
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Groq API Key### Installation
1. Clone the repository:
```bash
git clone <repository-url>
cd BudgetManage```
2. Install dependencies:```bash
pip install -r requirements.txt```
3. Create a `.env` file in the root directory:
```envTELEGRAM_TESTING_BOT_TOKEN=your_TELEGRAM_TESTING_BOT_TOKEN
GROQ_API_KEY=your_groq_api_keyMONGO_URI=your_mongodb_connection_string
MONGO_DB_NAME=your_database_name
SECRET_KEY=your_secret_key_for_password_hashing```
4. Run the bot:
```bash
python main.p```
## Usage 📱
### Getting Started
1. Start a chat with your bot on Telegram2. Use `/start` to begin
3. Register or login with your credentials4. Start tracking expenses!
### Adding Expenses
**Text Input:**
- "Coffee ₹50"
- "Lunch at restaurant 350"- "Movie tickets 400"
**Photo Input:**- Send a photo of your receipt
- The bot will extract item name, category, and price- Confirm or correct the details
### Categories
- 🍔 Food
- 🎬 Entertainment
- ✈️ Travel- 📦 Others

## Project Structure 
```BudgetManager/
├── main.py                 # Main bot application
├── langchain_bot.py        # LangGraph conversation flow├── message_to_json.py      # Expense parsing and handling
├── utils/│   └── crypto.py          # Password hashing utilities├── experiments/
│   ├── bot.py             # Experimental bot features
│   └── db_Connect.py      # Database connection testing
└── README.m```
## Key Components 🔑
### Authentication Flow- User registration with password hashing
- Secure login verification- Session management via context.user_data
### Expense Processing
1. Receipt/text input received2. AI extracts name, category, and price
3. Confidence-based verification
4. User confirmation or correction5. Storage in MongoDB
### LangGraph Integration- State-based conversation management
- Multi-turn clarification dialogs- Context-aware responses
## Security 🔒
- Passwords are hashed using bcrypt with HMAC pre-hashing
- Secret key-based password protection- Secure MongoDB connections
## Contributing 🤝
Contributions are welcome! Please feel free to submit a Pull Request.
## License 📄
[Add your license here]
## Support 💬

For issues and questions, please open an issue on GitHub.---

Made with ❤️ using Python, LangChain, and Telegram Bot API




















d




📁














y








r

















