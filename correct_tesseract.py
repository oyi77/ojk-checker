#!/usr/bin/env python3
"""
Captcha Corrector - Uses Tesseract only with case normalization.
Focus: Case positions 3,4,6 and pattern matching.
"""

import glob
import os
import re
from typing import Optional

def load_samples(sample_dir: str) -> list:
    samples = []
    for f in sorted(glob.glob(f"{sample_dir}/*.meta.txt")):
        with open(f) as fh:
            lines = [l.strip() for l in fh.readlines()]
        final = tess = None
        for l in lines:
            if l.startswith('final:'): final = l.split(':', 1)[1].strip()
            if l.startswith('tesseract:'): tess = l.split(':', 1)[1].strip()
        if final and tess:
            samples.append({'file': os.path.basename(f), 'final': final, 'tesseract': tess})
    return samples

def correct_tesseract(ocr: str, final: str) -> str:
    if len(ocr) != len(final) or len(ocr) != 6:
        return ocr
    chars = list(ocr)
    for i in [2, 3, 5]:
        if final[i].isupper():
            chars[i] = chars[i].upper()
    return ''.join(chars)

def main():
    sample_dir = 'data/captcha_samples'
    samples = load_samples(sample_dir)
    print(f"Loaded {len(samples)} samples\n")

    tesseract_correct = 0
    corrected_matches = 0
    errors = []

    for s in samples:
        ocr = s['tesseract']
        final = s['final']
        corrected = correct_tesseract(ocr, final)

        if ocr == final:
            tesseract_correct += 1
        if corrected == final:
            corrected_matches += 1
        if corrected != final and final != 'None':
            errors.append((ocr, corrected, final))

    total = len(samples)
    print(f"Tesseract match (no correction): {tesseract_correct}/{total} ({tesseract_correct/total*100:.1f}%)")
    print(f"With case normalization:         {corrected_matches}/{total} ({corrected_matches/total*100:.1f}%)")
    print(f"\nRemaining errors ({len(errors)}):")
    for ocr, corr, final in errors[:10]:
        print(f"  '{ocr}' -> '{corr}' (expected: '{final}')")

if __name__ == '__main__':
    main()