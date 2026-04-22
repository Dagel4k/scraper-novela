from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_FREE")
client = genai.Client(api_key=api_key)

print("Listing models with client.models.list():")
for model in client.models.list():
    print(f"- {model.name}")
