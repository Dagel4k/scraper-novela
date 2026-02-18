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
        files = sorted([f for f in os.listdir(self.eng_dir) if f.endswith('_en.txt')])
        
        for file in files:
            path = os.path.join(self.eng_dir, file)
            # Filename format: 1850_en.txt or 0001_en.txt
            try:
                ch_num = int(file.split('_')[0])
            except ValueError:
                continue

            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            self.eng_chapters.append({
                'num': ch_num,
                'text': content.strip()
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
            
            # Filename format: cn_0001.txt
            m = re.match(r'cn_(\d+)\.txt', file)
            if m:
                ch_num = int(m.group(1))
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

        # High-weight unique anchors
        unique_anchors = {
            "Liu Wenyan": "柳文彦",
            "Chen Hao": "陈浩",
            "Xia Longwu": "夏龙武",
            "Marquis Xia": "夏侯",
            "White Feng": "白枫",
            "Wu Wenhai": "吴文海",
            "Lightning Source Blade": "雷元刀",
            "Myriad Race Cult": "万族教",
            "Divine Character": "神文",
            "Cultural Research Academy": "文明学府",
            "War Academy": "战争学府",
            "Devil Subduing Army": "镇魔军",
            "Martial Dragon Guards": "龙武卫",
            "Willpower": "意志力",
            "Allheaven": "诸天",
            "Source Opening": "开元",
            "Great Strength": "千钧",
            "Infinite Strength": "万石",
            "Skysoar": "腾空",
            "Cloudstep": "凌云",
            "Mountainsea": "山海",
            "Sun and Moon": "日月",
            "Nanyuan": "南元",
            "Great Xia": "大夏",
            "Su Yu": "苏宇",
            "Su Long": "苏龙",
            "Liu Peng": "刘鹏",
            "Xia Bing": "夏冰",
            "Wu Lan": "吴岚",
            "Zhao Chuan": "赵川",
            "Talisman King": "符王",
            "Marquis Lanshan": "岚山侯",
            "Stable Army Marquis": "定军侯",
            "Great Zhou King": "大周王",
            "Friendly Su Yu": "和蔼的苏宇",
            "Zhu Tiandao": "朱天道",
            "Iron-winged bird": "铁翼鸟",
            "Rumble lightning beast": "霹雳雷霆兽",
            # Late novel anchors (Upper Realm arc)
            "Dingjun Marquis": "定军侯",
            "Lanshan Marquis": "岚山侯",
            "Zhennan Marquis": "镇南侯",
            "Minshan Marquis": "岷山侯",
            "Firecloud Marquis": "火云侯",
            "Great Zhou King": "大周王",
            "Wan Tiansheng": "万天圣",
            "Blue Sky": "蓝天",
            "Tianku": "天窟",
            "Yixian": "一线",
            "Upper Realm": "上界",
            "Necropolis Realm": "死灵界",
            "Human Sovereign": "人皇"
        }
        
        # Base ratio
        cn_en_ratio = 48.0 / 72.0
        
        current_cn_ptr = 0
        
        for i, eng in enumerate(tqdm(self.eng_chapters, desc="Aligning")):
            eng_num = eng['num']
            eng_text = eng['text']
            
            # Detect split parts
            first_lines = "\n".join(eng_text.splitlines()[:15])
            is_split_part = bool(re.search(r'\((2|3|4|5|6|7|8|9)\)', first_lines))
            
            best_cn_idx = current_cn_ptr
            max_score = -999999
            
            # Prediction logic based on historical ratio
            if is_split_part:
                predicted_cn_num = self.cn_chapters[current_cn_ptr]['num']
            else:
                # Use the global ratio as a baseline
                # Adjust ratio to prevent "drifting high"
                if eng_num < 200:
                    effective_ratio = 48.0 / 72.0 # ~0.66
                elif eng_num < 1000:
                    effective_ratio = 0.5
                else:
                    effective_ratio = 0.38 

                predicted_cn_num = round(eng_num * effective_ratio)
                if predicted_cn_num < 1:
                    predicted_cn_num = 1
                
                # Ensure it doesn't stay behind the last picked chapter if not split
                last_picked_cn = self.cn_chapters[current_cn_ptr]['num']
                if predicted_cn_num < last_picked_cn:
                     predicted_cn_num = last_picked_cn
            
            # Search window
            if current_cn_ptr >= len(self.cn_chapters) - 1:
                mapping[str(eng_num)] = self.cn_chapters[-1]['num']
                continue

            # Key fix: Allow looking back if prediction is significantly lower than current_ptr
            # This helps recover if we previously drifted too high.
            # But we still prefer forward continuity, so we don't go back too far unless score is great.
            
            # Start search from prediction or current, whichever is lower (with some buffer)
            start_search = max(0, min(current_cn_ptr, predicted_cn_num - 5))
            
            window_size = 50 
            end_search = min(len(self.cn_chapters), start_search + window_size)
            
            for idx in range(start_search, end_search):
                cn = self.cn_chapters[idx]
                cn_num = cn['num']
                cn_text = cn['text']
                
                # 1. Sequence Score (Softened further)
                dist = abs(cn_num - predicted_cn_num)
                num_score = -dist * 10 # Much less severe penalty to allow anchor matching
                
                # 2. Anchor match
                anchor_score = 0
                for en_name, cn_name in unique_anchors.items():
                    if en_name in eng_text and cn_name in cn_text:
                        weight = 15
                        if en_name in ["Friendly Su Yu"]:
                            weight = 1000
                        elif en_name in ["Wu Lan", "Liu Wenyan", "White Feng", "Lightning Source Blade", 
                                     "Zhao Chuan", "Talisman King", "Marquis Lanshan", "Stable Army Marquis", "Great Zhou King"]:
                            weight = 200
                        anchor_score += weight
                
                # 3. Continuity bonus
                cont_bonus = 0
                if is_split_part:
                    if idx == current_cn_ptr:
                        cont_bonus = 120
                else:
                    if idx == current_cn_ptr + 1:
                        cont_bonus = 30
                    elif idx == current_cn_ptr:
                        cont_bonus = 10
                
                total_score = num_score + anchor_score + cont_bonus

                if total_score > max_score:
                    max_score = total_score
                    best_cn_idx = idx
                


            mapping[str(eng_num)] = self.cn_chapters[best_cn_idx]['num']
            current_cn_ptr = best_cn_idx
                
        return mapping

    def save_mapping(self, mapping, output_file):
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(mapping, f, indent=4)
        print(f"Saved mapping to {output_file}")

if __name__ == "__main__":
    ENG_DIR = "output/tribulation"
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
