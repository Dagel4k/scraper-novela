
import json
import re

MASTER_PATH = 'data/master_names.json'

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    data = load_json(MASTER_PATH)
    fixed_count = 0
    
    # known manual fixes based on QA report
    manual_fixes = {
        "Spirit Suppression General": "General de Supresión Espiritual",
        "Immortal Battle Marquis": "Marqués de la Batalla Inmortal",
        "Blood Severing Marquis": "Marqués de la Sangre Cortada",
        "Primordial Giant King": "Rey de los Gigantes Primordiales",
        "Divine Pseudo Emperor": "Pseudo Emperador Divino",
        "South Heavenly King": "Rey Celestial del Sur",
        "East Heavenly Domain": "Dominio Celestial del Este",
        "Luminous Domain Mansion": "Mansión del Dominio Luminoso",
        "Celestial Dragon Marquis": "Marqués del Dragón Celestial",
        "Spirit Suppression Domain": "Dominio de Supresión Espiritual",
        "Heavenly Overseer Marquis": "Marqués del Vigía Celestial",
        "Cloud Water Marquis": "Marqués del Agua y las Nubes",
        "Fallen Star Marquis": "Marqués de la Estrella Caída",
        "Phoenix Pseudo Emperor": "Pseudo Emperador Fénix",
        "Fate Pseudo Emperor": "Pseudo Emperador del Destino",
        "Hou Pseudo Emperor": "Pseudo Emperador Hou",
        "Spatial Beast Pseudo Emperor": "Pseudo Emperador Bestia Espacial", # Check phrasing
        "Clearfilth Office": "Oficina de Limpieza de la Suciedad", # or similar
        "Grand Dream City": "Ciudad del Gran Sueño",
        "Heavendoom City": "Ciudad del Castigo Celestial",
        "Sky Sundering Saber": "Sable Rompe Cielos",
        "Talisman King": "Rey Talismán",
        "Culture King's Residence": "Residencia del Rey de la Cultura",
        "Three-headed demon wolf": "Lobo Demonio de Tres Cabezas",
    }
    
    # 1. Apply manual fixes & fix EN==ES
    for cn, entry in data.items():
        if isinstance(entry, dict):
            en = entry.get('en', '').strip()
            es = entry.get('es', '').strip()
            
            # Check manual fix list (by EN match)
            # Case insensitive lookup?
            for k, v in manual_fixes.items():
                if k.lower() == en.lower():
                    if es != v:
                        print(f"Fixing '{en}': '{es}' -> '{v}'")
                        entry['es'] = v
                        fixed_count += 1
            
            # Check for untranslated EN==ES (and not in manual list already handled)
            if en and es and en.lower() == es.lower():
                # heuristics: if it contains "General", "Marquis", "King", "Emperor" it should definitely generally change
                keywords = ["General", "Marquis", "King", "Emperor", "City", "Domain", "Realm"]
                if any(kw in en for kw in keywords):
                     print(f"WARNING: Untranslated term found: {en} -> {es} (CN: {cn}). Needs manual fix.")

    save_json(MASTER_PATH, data)
    print(f"Updated {fixed_count} entries.")

if __name__ == "__main__":
    main()
