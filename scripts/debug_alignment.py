
import json
import os
import re

ALIGNMENT_FILE = '/Users/daniel/Downloads/scraper novela/data/alignment_map.json'

def main():
    try:
        if not os.path.exists(ALIGNMENT_FILE):
             print(f"Alignment file not found: {ALIGNMENT_FILE}")
             return

        with open(ALIGNMENT_FILE, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading {ALIGNMENT_FILE}: {e}")
        return

    # Assuming data is dict { "cn_str": en_int }
    if not isinstance(data, dict):
        print(f"Expected dict in json, got {type(data)}")
        # Check if it has 'mapping' key which is a dict? No, based on head output it's direct.
        return

    # Reverse map to find EN for CN 700-704
    target_cn_chapters = [700, 701, 702, 703, 704]
    mapping = {}
    target_cn_set = set(target_cn_chapters)
    
    for en_str, cn_val in data.items():
        if cn_val in target_cn_set:
            # We want CN -> EN list (as one CN might map to multiple ENs)
            if cn_val not in mapping:
                mapping[cn_val] = []
            mapping[cn_val].append(en_str)

    print(f"Alignment found for {len(mapping)} chapters.")

    for cn in sorted(mapping.keys()):
        en_list = sorted(mapping[cn], key=lambda x: int(x))
        print(f"\nCN {cn} -> EN {en_list}")
        
        for en_num_str in en_list:
             try:
                 en_num_int = int(en_num_str)
                 en_path = f"/Users/daniel/Downloads/scraper novela/output/tribulation/{en_num_int:04d}_en.txt"
             except:
                 en_path = f"/Users/daniel/Downloads/scraper novela/output/tribulation/{en_num_str}_en.txt"

        if not os.path.exists(en_path):
             print(f"  EN file missing: {en_path}")
             continue

        try:
             with open(en_path, 'r', encoding='utf-8', errors='ignore') as f:
                 content = f.read()
                 
             # Check for keywords
             # Case insensitive search
             # Add more variations if needed
             target_words = ["Emperor Wu", "King Wu", "Martial Emperor", "Martial King", "Wu Huang", "Wu Wang", "Human Emperor", "King Wen"]
             found = False
             for kw in target_words:
                 matches = [m.start() for m in re.finditer(re.escape(kw), content, re.IGNORECASE)]
                 if matches:
                     print(f"  Found '{kw}': {len(matches)} times")
                     for i in matches[:3]:  # Show first 3 contexts
                         start_idx = max(0, i - 100) # larger context
                         end_idx = min(len(content), i + 100 + len(kw))
                         snippet = content[start_idx:end_idx].replace('\n', ' ')
                         print(f"    Context: ...{snippet}...")
                     found = True
             if not found:
                  print("  No relevant keywords found.")

        except Exception as e:
             print(f"  Error reading {en_path}: {e}")

if __name__ == "__main__":
    main()
