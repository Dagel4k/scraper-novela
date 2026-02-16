import os
import argparse
import re

def create_combined_file(input_dir, output_dir, start_chapter, count, filename):
    combined_content = []
    found_count = 0
    current = start_chapter
    
    # Heuristic: stop looking if we miss too many consecutive chapters
    missed_consecutive = 0
    max_missed = 20 

    end_chapter = start_chapter + count - 1
    
    print(f"Procesando rango: {start_chapter} - {end_chapter}...")

    # Iterate exactly through the requested range
    for current in range(start_chapter, start_chapter + count):
        chapter_filename = f"{current:04d}_en.txt"
        filepath = os.path.join(input_dir, chapter_filename)
        
        if os.path.exists(filepath):
            # print(f"  Agregando: {chapter_filename}")
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                combined_content.append(f"Chapter {current}\n\n{content}\n\n" + "="*40 + "\n\n")
            found_count += 1
            missed_consecutive = 0
        else:
            # print(f"  Advertencia: No se encontró {chapter_filename}")
            missed_consecutive += 1

    if combined_content:
        output_path = os.path.join(output_dir, filename)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("".join(combined_content))
        print(f"  -> Creado: {filename} ({found_count} capítulos)")
        return True, found_count
    else:
        print(f"  -> Rango vacío, no se creó archivo.")
        return False, 0

def main():
    parser = argparse.ArgumentParser(description="Combine chapters into single or multiple files.")
    parser.add_argument("--input-dir", required=True, help="Directory containing chapter files")
    parser.add_argument("--output-dir", required=True, help="Directory to save the combined files")
    parser.add_argument("--start", type=int, default=1, help="Start chapter number")
    parser.add_argument("--count", type=int, default=10, help="Number of chapters per file")
    parser.add_argument("--filename", help="Output filename (ignored if --batch-all is used)")
    parser.add_argument("--batch-all", action="store_true", help="Process all chapters in blocks of --count")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)
        
    if args.batch_all:
        current_start = args.start
        empty_blocks = 0
        
        while empty_blocks < 3: # Stop after 3 empty blocks
            end = current_start + args.count - 1
            # Auto-generate filename for batch
            batch_filename = f"novel_{current_start:04d}_{end:04d}.txt"
            
            success, found = create_combined_file(
                args.input_dir, 
                args.output_dir, 
                current_start, 
                args.count, 
                batch_filename
            )
            
            if success:
                empty_blocks = 0
            else:
                empty_blocks += 1
                
            current_start += args.count
            
        print("\nProcesamiento por lotes finalizado.")
        
    else:
        # Single mode
        fname = args.filename if args.filename else f"novel_{args.start:04d}_{args.start+args.count-1:04d}.txt"
        create_combined_file(args.input_dir, args.output_dir, args.start, args.count, fname)

if __name__ == "__main__":
    main()
