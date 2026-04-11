#!/usr/bin/env python3
import argparse
import os
import re
import zipfile
import uuid
from datetime import datetime

# Templates
CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

STYLE_CSS = """
body { font-family: sans-serif; }
h1 { text-align: center; }
p { text-indent: 1em; margin-bottom: 0.5em; }
"""

def create_epub(input_dir, output_file, title="Novel", author="Unknown", start_chap=None, end_chap=None):
    # Find chapters
    files = []
    for f in os.listdir(input_dir):
        if f.endswith(".txt"):
            # Try to extract chapter number
            m = re.search(r"(?:^|cn_)(\d+)(?:_es)?", f)
            if m:
                chap_num = int(m.group(1))
                if start_chap is not None and chap_num < start_chap:
                    continue
                if end_chap is not None and chap_num > end_chap:
                    continue
                files.append((chap_num, os.path.join(input_dir, f)))
    
    files.sort(key=lambda x: x[0])
    
    if not files:
        print("No chapter files found.")
        return

    # Create EPUB
    with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Mimetype (must be first and uncompressed)
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        
        # META-INF/container.xml
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        
        # OEBPS/Styles/style.css
        zf.writestr("OEBPS/Styles/style.css", STYLE_CSS)
        
        # Content
        manifest = []
        spine = []
        toc = []
        
        # Title page
        title_html = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<title>{title}</title>
<link href="../Styles/style.css" rel="stylesheet" type="text/css"/>
</head>
<body>
<h1>{title}</h1>
<p style="text-align: center;">{author}</p>
</body>
</html>"""
        zf.writestr("OEBPS/Text/title.xhtml", title_html)
        manifest.append('<item id="title" href="Text/title.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="title"/>')
        
        # Chapters
        for i, (chap_num, path) in enumerate(files):
            with open(path, 'r', encoding='utf-8') as f:
                raw_text = f.read()
            
            # Basic HTML escaping and formatting
            content = ""
            lines = raw_text.splitlines()
            chapter_title = f"Chapter {chap_num}"
            
            # Try to find a real title in the first few lines
            found_title = False
            for line in lines[:5]:
                if line.strip():
                    chapter_title = line.strip()
                    found_title = True
                    break
            
            # If we used the first line as title, skip it in body? 
            # Ideally yes, but let's just keep it simple.
            
            body_content = ""
            for line in lines:
                if line.strip():
                    body_content += f"<p>{html_escape(line.strip())}</p>\n"
            
            html = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<title>{chapter_title}</title>
<link href="../Styles/style.css" rel="stylesheet" type="text/css"/>
</head>
<body>
<h1>{chapter_title}</h1>
{body_content}
</body>
</html>"""
            
            filename = f"chapter_{chap_num}.xhtml"
            zf.writestr(f"OEBPS/Text/{filename}", html)
            
            item_id = f"chap{chap_num}"
            manifest.append(f'<item id="{item_id}" href="Text/{filename}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{item_id}"/>')
            toc.append((item_id, filename, chapter_title))
            
        # OEBPS/content.opf
        unique_id = str(uuid.uuid4())
        opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:language>es</dc:language>
    <dc:identifier id="BookId">urn:uuid:{unique_id}</dc:identifier>
    <dc:creator>{author}</dc:creator>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="style" href="Styles/style.css" media-type="text/css"/>
    {chr(10).join(manifest)}
  </manifest>
  <spine toc="ncx">
    {chr(10).join(spine)}
  </spine>
</package>"""
        zf.writestr("OEBPS/content.opf", opf)
        
        # OEBPS/toc.ncx
        nav_points = ""
        for i, (item_id, filename, title_text) in enumerate(toc):
            nav_points += f"""
    <navPoint id="navPoint-{i+1}" playOrder="{i+1}">
      <navLabel>
        <text>{html_escape(title_text)}</text>
      </navLabel>
      <content src="Text/{filename}"/>
    </navPoint>"""
            
        ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{unique_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>{title}</text>
  </docTitle>
  <navMap>
    {nav_points}
  </navMap>
</ncx>"""
        zf.writestr("OEBPS/toc.ncx", ncx)
        
    print(f"Created EPUB: {output_file}")

def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def main():
    parser = argparse.ArgumentParser(description="Generate EPUB from text chapters.")
    parser.add_argument("--input", required=True, help="Input directory")
    parser.add_argument("--output", required=True, help="Output file")
    parser.add_argument("--title", default="Novel", help="Title of the novel")
    parser.add_argument("--author", default="Unknown", help="Author of the novel")
    parser.add_argument("--start", type=int, help="Start chapter number")
    parser.add_argument("--end", type=int, help="End chapter number")
    
    args = parser.parse_args()
    
    if not os.path.exists(os.path.dirname(args.output)):
        os.makedirs(os.path.dirname(args.output))
        
    create_epub(args.input, args.output, args.title, args.author, args.start, args.end)

if __name__ == "__main__":
    main()
