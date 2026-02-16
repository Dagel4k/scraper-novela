import os
import re
import json
from tqdm import tqdm

class NovelAligner:
    def __init__(self, eng_dir, cn_dir):
        self.eng_dir = eng_dir
        self.cn_dir = cn_dir
        self.eng_chapters = []
        self.cn_chapters = []

    def load_eng_chapters(self):
        print("Loading English chapters...")
        files = sorted([f for f in os.listdir(self.eng_dir) if f.startswith('novel_') and f.endswith('.txt')])
        
        for file in files:
            path = os.path.join(self.eng_dir, file)
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            chunks = re.split(r'={10,}', content)
            for chunk in chunks:
                match = re.search(r'Chapter (\d+)', chunk)
                if match:
                    ch_num = int(match.group(1))
                    self.eng_chapters.append({
                        'num': ch_num,
                        'text': chunk.strip()
                    })
        
        self.eng_chapters.sort(key=lambda x: x['num'])
        print(f"Loaded {len(self.eng_chapters)} English chapters.")

    def load_cn_chapters(self):
        print("Loading Chinese chapters...")
        files = sorted([f for f in os.listdir(self.cn_dir) if f.startswith('cn_') and f.endswith('.txt')])
        
        for file in files:
            path = os.path.join(self.cn_dir, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Key priority: "第X章"
            match = re.search(r'第(\d+)章', content)
            if match:
                ch_num = int(match.group(1))
            else:
                # Fallback to any number in the first part
                m2 = re.search(r'(\d+)', content[:100])
                ch_num = int(m2.group(1)) if m2 else 0

            self.cn_chapters.append({
                'num': ch_num,
                'text': content.strip(),
                'filename': file
            })
        
        self.cn_chapters.sort(key=lambda x: x['num'])
        print(f"Loaded {len(self.cn_chapters)} Chinese chapters.")

    def align(self):
        print("Starting precision alignment...")
        mapping = {}
        
        if not self.eng_chapters or not self.cn_chapters:
            return {}

        anchors = {
            "Su Yu": "苏宇",
            "Su Long": "苏龙",
            "Liu Wenyan": "柳文彦",
            "Nanyuan": "南元"
        }
        
        current_cn_ptr = 0
        
        for eng in tqdm(self.eng_chapters, desc="Aligning"):
            eng_num = eng['num']
            
            # Find the best Chinese chapter in a reasonable window around the current pointer
            # We look at up to 10 Chinese chapters from the current one
            best_cn_idx = current_cn_ptr
            max_score = -999999
            
            # Since translations can be behind (Eng 10 maps to CN 9), 
            # we allow for some offset.
            
            # Search window: [current, current + 5]
            window_size = 5
            end_idx = min(len(self.cn_chapters), current_cn_ptr + window_size)
            
            for idx in range(current_cn_ptr, end_idx):
                cn = self.cn_chapters[idx]
                cn_num = cn['num']
                
                # Base score: inverse distance of chapter numbers
                # Highly weighted to keep numbers roughly aligned
                score = -abs(eng_num - cn_num) * 10
                
                # Bonus for anchor matching (tie breaker and validation)
                for en_name, cn_name in anchors.items():
                    if en_name in eng['text'] and cn_name in cn['text']:
                        score += 2
                
                # Bonus if it's the SAME Chinese chapter as we are currently on (1-to-N)
                if idx == current_cn_ptr:
                    score += 5

                if score > max_score:
                    max_score = score
                    best_cn_idx = idx

            # If the best one is at the end of our raws, we continue mapping to it
            # until we eventually stop if the score gets too bad? 
            # Actually, the user wants us to stop if we run out.
            
            # Stop if the chapter number gap is too large (> 5)
            if abs(eng_num - self.cn_chapters[best_cn_idx]['num']) > 20 and eng_num > 50:
                # Stop mapping if we are clearly out of range
                break

            mapping[str(eng_num)] = self.cn_chapters[best_cn_idx]['num']
            current_cn_ptr = best_cn_idx
                
        return mapping

    def save_mapping(self, mapping, output_file):
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(mapping, f, indent=4)
        print(f"Saved mapping to {output_file}")

if __name__ == "__main__":
    ENG_DIR = "TXT_Notebook"
    CN_DIR = "data/cn_raws"
    OUTPUT = "data/alignment_map.json"
    
    aligner = NovelAligner(ENG_DIR, CN_DIR)
    aligner.load_eng_chapters()
    aligner.load_cn_chapters()
    
    if aligner.eng_chapters and aligner.cn_chapters:
        mapping = aligner.align()
        aligner.save_mapping(mapping, OUTPUT)
    else:
        print("Missing data for alignment.")
