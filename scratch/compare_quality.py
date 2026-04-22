import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Add current directory to path so we can import adapters
sys.path.append(str(Path(__file__).parent.parent))

from adapters.gemini_adapter import GeminiAdapter
import asyncio

async def test_quality(model_name, source_text):
    load_dotenv()
    api_key = os.getenv("GEMINI_API_FREE")
    if not api_key:
        print(f"Error: GEMINI_API_FREE not found in .env")
        return

    print(f"Testing quality for {model_name}...")
    
    adapter = GeminiAdapter(model=model_name, api_key=api_key)
    
    system_prompt = """Eres un traductor literario experto del CHINO al ESPAÑOL neutro (Latinoamérica).
Tu objetivo es traducir novelas de "Cultivo/Xianxia" de forma fluida, épica y natural.
Traduce con fidelidad, sin resumir; conserva el tono narrativo, la marcialidad y los matices del texto original.
Preserva párrafos y líneas en blanco exactamente como en el original.
Usa español neutro: 'ustedes', 'de acuerdo', 'tomar' (evita modismos de España)."""

    user_message = f"Traduce fielmente el siguiente fragmento. Responde EN ESPAÑOL. Devuelve SOLO el texto traducido, sin notas ni etiquetas.\n\n{source_text}"
    
    try:
        response = await adapter._call_api(
            system_prompt=system_prompt,
            user_text=user_message,
            temperature=0.3,
            timeout=60.0
        )
        print(f"\n--- {model_name} Result ---\n{response}\n")
        return response
    except Exception as e:
        print(f"Error with {model_name}: {e}")
        return None

async def main():
    # Fragmento extraído de cn_0802.txt
    source_text = """第784章 全员复苏（求订阅）

  死灵大道中。

  苏宇趁着亡灵之主消失，迅速开始复生众人，雷霆四起！

  生死之力，被苏宇大量抽离，抽的整个生死交界之地，都在颤动。

  第二个复苏的是南王！
  南王的复苏，雷霆劫难强大的不可思议，一道道雷霆之力，劈的苏宇的天地感觉都要被粉碎了！

  南王撑过去了！

  从焦炭状态，恢复了过来。

  恢复过来的南王，和岚山侯一样，换了装束，不再是之前那死灵模样，此刻的南王，面色依旧清冷，隐约和之前差不多的模样，不过看起来更加冷漠一些。"""
    
    # Probamos con Flash 2.5 estándar (no lite) para ver la diferencia de calidad
    await test_quality("gemini-2.5-flash", source_text)

if __name__ == "__main__":
    asyncio.run(main())
