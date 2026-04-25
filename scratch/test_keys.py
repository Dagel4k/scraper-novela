import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

def test_key(key_name):
    api_key = os.getenv(key_name)
    if not api_key:
        print(f"{key_name}: NOT FOUND")
        return False
    print(f"Testing {key_name} ({api_key[:10]}...)", end=" ", flush=True)
    client = genai.Client(api_key=api_key)
    try:
        # Try a tiny request
        res = client.models.generate_content(model='gemini-2.0-flash', contents='OK')
        print(f"SUCCESS: {res.text.strip()}")
        return True
    except Exception as e:
        print(f"FAIL: {str(e)[:50]}...")
        return False

if __name__ == "__main__":
    t_free = test_key('GEMINI_API_FREE')
    t_key = test_key('GEMINI_API_KEY')
    
    if not t_free and not t_key:
        print("\nAMBAS CLAVES ESTÁN BLOQUEADAS O SIN CUOTA.")
    elif t_key:
        print("\nRECOMENDACIÓN: CAMBIAR A GEMINI_API_KEY.")
