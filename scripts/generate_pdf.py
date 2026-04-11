#!/usr/bin/env python3
import argparse
import os
import re
from fpdf import FPDF

class NovelPDF(FPDF):
    def header(self):
        if hasattr(self, 'chapter_title'):
            self.set_font('helvetica', 'I', 8)
            self.cell(0, 10, self.chapter_title, 0, 0, 'R')
            self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('helvetica', 'I', 8)
        self.cell(0, 10, f'Página {self.page_no()}', 0, 0, 'C')

def create_pdf(input_dir, output_file, title="Novel", author="Unknown", start_chap=None, end_chap=None):
    pdf = NovelPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Title Page
    pdf.add_page()
    pdf.set_font('helvetica', 'B', 24)
    pdf.cell(0, 60, title, 0, 1, 'C')
    pdf.set_font('helvetica', '', 16)
    pdf.cell(0, 10, author, 0, 1, 'C')
    
    # Find chapters
    files = []
    for f in os.listdir(input_dir):
        if f.endswith(".txt"):
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

    for chap_num, path in files:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        chapter_title = f"Capítulo {chap_num}"
        content_lines = lines
        
        if lines:
            first_line = lines[0].strip()
            if first_line:
                chapter_title = first_line
                content_lines = lines[1:]

        pdf.chapter_title = chapter_title
        pdf.add_page()
        pdf.set_font('helvetica', 'B', 16)
        pdf.multi_cell(0, 10, chapter_title, 0, 'C')
        pdf.ln(10)
        
        pdf.set_font('helvetica', '', 12)
        for line in content_lines:
            text = line.strip()
            if text:
                # Basic encoding fix for fpdf2
                try:
                    pdf.multi_cell(0, 7, text)
                    pdf.ln(3)
                except:
                    # Fallback for characters that might break helvetica
                    safe_text = text.encode('latin-1', 'replace').decode('latin-1')
                    pdf.multi_cell(0, 7, safe_text)
                    pdf.ln(3)

    pdf.output(output_file)
    print(f"Created PDF: {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Generate PDF from text chapters.")
    parser.add_argument("--input", required=True, help="Input directory")
    parser.add_argument("--output", required=True, help="Output file")
    parser.add_argument("--title", default="Novel", help="Title of the novel")
    parser.add_argument("--author", default="Unknown", help="Author of the novel")
    parser.add_argument("--start", type=int, help="Start chapter number")
    parser.add_argument("--end", type=int, help="End chapter number")
    
    args = parser.parse_args()
    
    if not os.path.exists(os.path.dirname(args.output)):
        os.makedirs(os.path.dirname(args.output))
        
    create_pdf(args.input, args.output, args.title, args.author, args.start, args.end)

if __name__ == "__main__":
    main()
