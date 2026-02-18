import json
import os
import shutil
import re

# Paths
project_root = os.getcwd()
source_dir = os.path.join(project_root, 'traduccion_cn')
target_app_dir = os.path.join(project_root, 'reader-app')
public_dir = os.path.join(target_app_dir, 'public')
chapters_dir = os.path.join(public_dir, 'chapters')
metadata_file = os.path.join(public_dir, 'chapters.json')

def extract_chapter_info(file_path):
    """Extracts chapter number and title from the first line of the file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            # Match formats like "Capítulo 689: El afable Su Yu"
            match = re.search(r'Capítulo\s+(\d+):?\s*(.*)', first_line)
            if match:
                number = int(match.group(1))
                title = match.group(2).strip()
                return number, title if title else f"Capítulo {number}"
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
    return None, None

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
