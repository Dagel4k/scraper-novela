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
            token = f"ZPROPER_NAME_{i:03d}Z" # More unique, using padding to avoid prefix issues
            placeholders[token] = eng_term
            processed_text = processed_text.replace(cn_term, token)
            
        return processed_text, placeholders

    def preserve_terms_post(self, text, placeholders):
        final_text = text
        # Sort tokens by length descending (if they varied) to be safe, 
        # but with Z...Z they are all same length.
        # Still, we must avoid MT translation of ZPROPER_NAME
        
        for token, eng_term in placeholders.items():
            # The MT engine might have changed "ZPROPER_NAME" to something else, 
            # or removed the 'Z', or added spaces.
            # We look for the index padded with 0s.
            match = re.search(r'ZPROPER_NAME_(\d+)Z', token)
            if match:
                idx = match.group(1)
                # Regex logic: Match any variation of Z PROPER NAME [idx] Z
                # Allow for spaces after Z, or underscores
                pattern_str = r'Z[ _-]*PROPER[ _-]*NAME[ _-]*' + re.escape(idx) + r'[ _-]*Z'
                pattern = re.compile(pattern_str, re.IGNORECASE)
                final_text = pattern.sub(eng_term, final_text)
            else:
                # Fallback
                final_text = final_text.replace(token, eng_term)
        return final_text

    def translate_chapter(self, cn_text):
        if not cn_text.strip(): return ""
        
        # 1. Pre-process Chinese with tokens
        processed_cn, placeholders = self.preserve_terms_pre(cn_text)
        
        # 2. Chinese (with tokens) -> English
        en_text = self.translate_internal(processed_cn, "zh", "en")
        
        # 3. English (with tokens) -> Spanish
        es_text = self.translate_internal(en_text, "en", "es")
        
        # 4. Restore terms into final Spanish text
        final_es = self.preserve_terms_post(es_text, placeholders)
        
        return final_es

    def batch_translate(self, cn_dir, output_dir, limit=5):
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        files = sorted([f for f in os.listdir(cn_dir) if f.startswith('cn_') and f.endswith('.txt')])[:limit]
        
        for file in tqdm(files, desc="Translating to Spanish"):
            input_path = os.path.join(cn_dir, file)
            output_name = file.replace('cn_', 'es_')
            output_path = os.path.join(output_dir, output_name)
            
            with open(input_path, 'r', encoding='utf-8') as f:
                cn_content = f.read()
            
            paragraphs = cn_content.split('\n')
            es_paragraphs = []
            for p in paragraphs:
                if p.strip():
                    es_paragraphs.append(self.translate_chapter(p))
                else:
                    es_paragraphs.append("")
            
            es_content = '\n'.join(es_paragraphs)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(es_content)

if __name__ == "__main__":
    CN_DIR = "data/cn_raws"
    OUTPUT_DIR = "data/es_translations"
    
    translator = NovelTranslator()
    print("Running FINAL Robust Translation Batch...")
    translator.batch_translate(CN_DIR, OUTPUT_DIR, limit=5)
    print(f"Batch Done! check {OUTPUT_DIR}")
