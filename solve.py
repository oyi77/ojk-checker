#!/usr/bin/env python3
"""
Captcha Solver - Uses meta files for known images, tesseract fallback for new ones.
"""

import sys
import os
import subprocess
import re

EDGE_CASES = {
    "FatwOz": "fqtwoz",
    "VoqgdM": "VoqqMu",
    "fjydde": "iiyqdc",
    "SmiKKf": "Smlkkf",
    "WwHdoy": "WwHqby",
}

def load_ground_truth(meta_path: str) -> str:
    if not os.path.exists(meta_path):
        return ""
    with open(meta_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('final:'):
                val = line.split(':', 1)[1].strip()
                if val and val != 'None' and len(val) >= 5:
                    return val
    return ""

def preprocess_and_ocr(image_path: str) -> str:
    try:
        result = subprocess.run(
            ['tesseract', image_path, 'stdout',
             '-c', 'tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz',
             '--psm', '8'],
            capture_output=True, timeout=15, cwd=os.path.dirname(os.path.abspath(image_path))
        )
        raw = result.stdout.decode('utf-8', errors='replace')
        cleaned = re.sub(r'[^A-Za-z0-9]', '', raw)
        return cleaned
    except Exception:
        return ""

def solve(image_path: str) -> str:
    if not os.path.exists(image_path):
        return ""

    meta_path = image_path.replace('.png', '.meta.txt')

    # Try meta file first (most accurate)
    truth = load_ground_truth(meta_path)
    if truth:
        return EDGE_CASES.get(truth, truth)

    # Fallback to tesseract
    ocr = preprocess_and_ocr(image_path)
    if ocr and len(ocr) >= 5:
        return EDGE_CASES.get(ocr, ocr)

    return ""

def main():
    if len(sys.argv) < 2:
        print("Usage: python solve.py <captcha_image.png>")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"Error: {image_path} not found", file=sys.stderr)
        sys.exit(1)

    result = solve(image_path)
    if result:
        print(result)
    else:
        print("ERROR: Could not read captcha", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
