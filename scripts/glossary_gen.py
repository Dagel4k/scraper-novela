import os
import json
import re
import spacy
from collections import Counter
from tqdm import tqdm
import math
import jieba

class GlossaryGenerator:
    def __init__(self, eng_dir, cn_dir, alignment_path):
        self.eng_dir = eng_dir
        self.cn_dir = cn_dir
        with open(alignment_path, 'r', encoding='utf-8') as f:
            self.alignment_map = json.load(f)
        
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except:
            print("Error loading spaCy model.")
            self.nlp = None

        self.glossary = {}
        self.seed_glossary = {
            "Su Yu": "苏宇",
            "Su Long": "苏龙",
            "Liu Wenyan": "柳文彦",
            "Nanyuan": "南元",
            "Great Xia": "大夏",
            "Chen Hao": "陈浩"
        }

    def load_chapters(self):
        eng_chapters = {}
        cn_chapters = {}
        
        files = [f for f in os.listdir(self.eng_dir) if f.startswith('novel_') and f.endswith('.txt')]
        for file in files:
            path = os.path.join(self.eng_dir, file)
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            chunks = re.split(r'={10,}', content)
            for chunk in chunks:
                match = re.search(r'Chapter (\d+)', chunk)
                if match:
                    ch_num = str(match.group(1))
                    eng_chapters[ch_num] = chunk.strip()

        files = [f for f in os.listdir(self.cn_dir) if f.startswith('cn_') and f.endswith('.txt')]
        for file in files:
            path = os.path.join(self.cn_dir, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            match = re.search(r'第(\d+)章', content)
            if match:
                ch_num = str(match.group(1))
                cn_chapters[ch_num] = content.strip()
        
        return eng_chapters, cn_chapters

    def get_entities(self, text):
        if not self.nlp: return []
        doc = self.nlp(text)
        # Focus on Person, GPE (Location), ORG
        entities = [ent.text.strip() for ent in doc.ents if ent.label_ in ["PERSON", "ORG", "GPE"]]
        # Clean: remove small, non-capitalized or obviously non-names
        entities = [ent for ent in entities if len(ent) > 2 and ent[0].isupper()]
        return list(set(entities))

    def generate(self, output_path):
        eng_chapters, cn_chapters = self.load_chapters()
        
        entity_occurrences = {} # {eng_entity: set(chapter_indices)}
        cn_word_occurrences = {} # {cn_word: set(chapter_indices)}
        chapter_indices = []

        print("Indexing aligned chapters and segmenting Chinese...")
        pair_idx = 0
        for eng_ch, cn_ch_num in self.alignment_map.items():
            try:
                if int(eng_ch) > 300: continue 
            except: continue

            if eng_ch in eng_chapters and str(cn_ch_num) in cn_chapters:
                eng_text = eng_chapters[eng_ch]
                cn_text = cn_chapters[str(cn_ch_num)]
                
                entities = self.get_entities(eng_text)
                for ent in entities:
                    if ent not in entity_occurrences: entity_occurrences[ent] = set()
                    entity_occurrences[ent].add(pair_idx)
                
                # Use jieba for proper segmentation
                words = jieba.lcut(cn_text)
                # Filter for words (remove punctuation, numbers, single-char words that aren't common in names)
                words = [w for w in words if len(w) >= 2 and not re.search(r'[0-9\s\W]', w)]
                
                for w in set(words):
                    if w not in cn_word_occurrences: cn_word_occurrences[w] = set()
                    cn_word_occurrences[w].add(pair_idx)
                
                chapter_indices.append(pair_idx)
                pair_idx += 1

        total_chapters = len(chapter_indices)
        if total_chapters == 0:
            print("No aligned chapters found!")
            return

        final_glossary = self.seed_glossary.copy()
        
        print(f"Analyzing correlations for {len(entity_occurrences)} entities across {total_chapters} chapters...")
        
        for ent, en_indices in tqdm(entity_occurrences.items()):
            if ent in final_glossary: continue
            if len(en_indices) < 2: continue # Even 2 might show correlation
            
            best_word = None
            max_score = -1
            
            for word, word_indices in cn_word_occurrences.items():
                if len(word_indices) < 2: continue
                
                intersection = en_indices.intersection(word_indices)
                if not intersection: continue
                
                # Lift score: P(Word|Entity) / P(Word)
                p_word_given_ent = len(intersection) / len(en_indices)
                p_word = len(word_indices) / total_chapters
                lift = p_word_given_ent / p_word
                
                # Score = Lift * log(volume)
                score = lift * math.log(len(intersection) + 1)
                
                if score > max_score:
                    max_score = score
                    best_word = word
            
            # Thresholding for quality
            if best_word and max_score > 3.0: 
                final_glossary[ent] = best_word

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_glossary, f, indent=4, ensure_ascii=False)
        print(f"Glossary saved to {output_path}. Total terms: {len(final_glossary)}")

if __name__ == "__main__":
    ENG_DIR = "TXT_Notebook"
    CN_DIR = "data/cn_raws"
    ALIGNMENT = "data/alignment_map.json"
    OUTPUT = "data/glossary.json"
    
    gen = GlossaryGenerator(ENG_DIR, CN_DIR, ALIGNMENT)
    gen.generate(OUTPUT)
