import json
import os
import shutil
import re

# Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
source_dir = os.path.join(project_root, 'traduccion_cn')
target_app_dir = os.path.join(project_root, 'reader-app')
public_dir = os.path.join(target_app_dir, 'public')
chapters_dir = os.path.join(public_dir, 'chapters')
metadata_file = os.path.join(public_dir, 'chapters.json')

def extract_chapter_info(file_path):
    """Extracts chapter number and title from the file content or filename."""
    number = None
    title = None
    
    # 1. Try to find in content (first 10 lines)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for _ in range(10):
                line = f.readline()
                if not line: break
                line = line.strip()
                # Match "Capítulo 123: Title" or "Capítulo 123 Title" or just "Capítulo 123"
                match = re.search(r'Cap[ií]tulo\s+(\d+)(?::|\s+)?\s*(.*)', line, re.IGNORECASE)
                if match:
                    number = int(match.group(1))
                    found_title = match.group(2).strip()
                    if found_title:
                        title = found_title
                    break
    except Exception as e:
        print(f"Error reading {file_path}: {e}")

    # 2. If not found in content, try filename
    if number is None:
        filename = os.path.basename(file_path)
        match = re.search(r'(?:^|cn_)(\d+)(?:_es)?', filename)
        if match:
            number = int(match.group(1))
    
    # 3. Construct title if missing
    if number is not None and not title:
        title = f"Capítulo {number}"
        
    return number, title

def prepare_data():
    if not os.path.exists(chapters_dir):
        os.makedirs(chapters_dir)
    else:
        # Clear existing chapters to avoid mixing sources
        shutil.rmtree(chapters_dir)
        os.makedirs(chapters_dir)
    
    chapters_metadata = []
    
    print(f"Reading files from {source_dir}...")
    files = [f for f in os.listdir(source_dir) if f.endswith('.txt')]
    
    for filename in files:
        src_file = os.path.join(source_dir, filename)
        number, title = extract_chapter_info(src_file)
        
        if number is not None:
            chapter_info = {
                "number": number,
                "title": title,
                "file": filename
            }
            chapters_metadata.append(chapter_info)
            
            # Copy file
            dest_file = os.path.join(chapters_dir, filename)
            shutil.copy2(src_file, dest_file)
            print(f"Added Chapter {number}: {title}")
        else:
            print(f"Skipping {filename}: Could not parse title/number.")

    # Sort by chapter number
    chapters_metadata.sort(key=lambda x: x["number"])

    print(f"Writing metadata to {metadata_file}...")
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(chapters_metadata, f, ensure_ascii=False, indent=2)

    print("Data preparation complete.")

if __name__ == "__main__":
    prepare_data()
