import os
import json
import re
import argostranslate.package
import argostranslate.translate
from tqdm import tqdm

class NovelTranslator:
    def __init__(self, glossary_path=None):
        # Strict, verified glossary for core characters, locations, and RENOWNED REALMS
        self.verified_glossary = {
            # Characters
            "苏宇": "Su Yu",
            "苏龙": "Su Long",
            "柳文彦": "Liu Wenyan",
            "陈浩": "Chen Hao",
            "万天圣": "Wan Tiansheng",
            "夏龙武": "Xia Longwu",
            "夏侯爷": "Marquis Xia",
            
            # Locations
            "南元": "Nanyuan",
            "大夏": "Great Xia",
            "大夏府": "Mansión Great Xia",
            "南元中等学府": "Academia Secundaria Nanyuan",
            "诸天战场": "Campo de Batalla Allheaven",
            
            # Cultivation Realms
            "开元境": "Reino Kaiyuan",
            "千钧境": "Reino Thousand-Jin (Mil Jin)",
            "万石境": "Reino Myriad Stone",
            "腾空境": "Reino Skysoar (Vuelo Celeste)",
            "凌云境": "Reino Cloudbreach (Nube Elevada)",
            "山海境": "Reino Mountainsea (Montaña y Mar)",
            "日月境": "Reino Sunmoon (Sol y Luna)",
            "永恒境": "Reino Eternal (Eternidad)",
            
            # Other Terms
            "意志力": "Fuerza de Voluntad",
            "元气": "Qi de Origen",
            "精血": "Sangre de Esencia",
            "功法": "Método de Cultivo",
            "神文": "Caracter Divino"
        }
        
        # Add simpler versions without '境'
        realms_simple = {
            "千钧": "Thousand-Jin",
            "万石": "Myriad Stone",
            "腾空": "Skysoar",
            "凌云": "Cloudbreach",
            "山海": "Mountainsea",
            "日月": "Sunmoon"
        }
        for cn, en in realms_simple.items():
            if cn not in self.verified_glossary:
                self.verified_glossary[cn] = en

        # Load dynamic glossary from glossary.json
        if glossary_path and os.path.exists(glossary_path):
            with open(glossary_path, 'r', encoding='utf-8') as f:
                dynamic_glossary_en_cn = json.load(f)
            
            # Reverse to CN -> EN
            for en, cn in dynamic_glossary_en_cn.items():
                if cn not in self.verified_glossary:
                    self.verified_glossary[cn] = en
            
            print(f"Loaded {len(dynamic_glossary_en_cn)} dynamic terms.")

    def translate_internal(self, text, from_code, to_code):
        try:
            return argostranslate.translate.translate(text, from_code, to_code)
        except Exception as e:
            print(f"Translation error: {e}")
            return text

    def preserve_terms_pre(self, text):
        placeholders = {}
        processed_text = text
        sorted_cn_terms = sorted(self.verified_glossary.keys(), key=len, reverse=True)
        
        for i, cn_term in enumerate(sorted_cn_terms):
            eng_term = self.verified_glossary[cn_term]
            token = f"X{i}X" # Simpler token: X123X. No spaces, distinct.
            placeholders[token] = eng_term
            processed_text = processed_text.replace(cn_term, token)
            
        return processed_text, placeholders

    def preserve_terms_post(self, text, placeholders):
        final_text = text
        for token, eng_term in placeholders.items():
            # Robust regex to catch X 123 X variations introduced by MT
            # Match X followed by optional spaces, then ID, then optional spaces, then X
            # Token format is X{i}X
            idx = token[1:-1] # Extract ID from X...X
            
            # Pattern: X \s* ID \s* X
            pattern_str = r'X\s*' + re.escape(idx) + r'\s*X'
            pattern = re.compile(pattern_str, re.IGNORECASE)
            
            final_text = pattern.sub(eng_term, final_text)
            
        return final_text

    def translate_chapter(self, cn_text, debug=False):
        if not cn_text.strip(): return ""
        
        # 1. Pre-process Chinese with tokens
        processed_cn, placeholders = self.preserve_terms_pre(cn_text)
        if debug: print(f"DEBUG [CN+Tokens]: {processed_cn[:100]}...")
        
        # 2. Chinese (with tokens) -> English
        en_text = self.translate_internal(processed_cn, "zh", "en")
        if debug: print(f"DEBUG [EN+Tokens]: {en_text[:100]}...")
        
        # 3. English (with tokens) -> Spanish
        es_text = self.translate_internal(en_text, "en", "es")
        if debug: print(f"DEBUG [ES+Tokens]: {es_text[:100]}...")
        
        # 4. Restore terms into final Spanish text
        final_es = self.preserve_terms_post(es_text, placeholders)
        if debug: print(f"DEBUG [Final]: {final_es[:100]}...")
        
        return final_es

    def batch_translate(self, cn_dir, output_dir, limit=5):
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        files = sorted([f for f in os.listdir(cn_dir) if f.startswith('cn_') and f.endswith('.txt')])[:limit]
        
        for i, file in enumerate(tqdm(files, desc="Translating to Spanish")):
            input_path = os.path.join(cn_dir, file)
            output_name = file.replace('cn_', 'es_')
            output_path = os.path.join(output_dir, output_name)
            
            with open(input_path, 'r', encoding='utf-8') as f:
                cn_content = f.read()
            
            # Debug first paragraph of first file
            is_first = (i == 0)
            
            paragraphs = cn_content.split('\n')
            es_paragraphs = []
            for j, p in enumerate(paragraphs):
                if p.strip():
                    # Check if it's a substantive paragraph for debug
                    debug_this = (is_first and j < 3 and len(p) > 20)
                    es_paragraphs.append(self.translate_chapter(p, debug=debug_this))
                else:
                    es_paragraphs.append("")
            
            es_content = '\n'.join(es_paragraphs)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(es_content)

if __name__ == "__main__":
    CN_DIR = "data/cn_raws"
    OUTPUT_DIR = "data/es_translations"
    
    translator = NovelTranslator(glossary_path="data/glossary.json")
    print("Running FINAL Robust Translation Batch...")
    translator.batch_translate(CN_DIR, OUTPUT_DIR, limit=1)
    print(f"Batch Done! check {OUTPUT_DIR}")
