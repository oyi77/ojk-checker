#!/usr/bin/env python3
"""
Captcha Correction Script - Applies transformation rules from data analysis.
"""

import os
import re
import glob
from typing import Dict, List, Tuple
from dataclasses import dataclass, field


@dataclass
class OCROutput:
    tesseract: str = ""
    ddddocr: str = ""
    easyocr: str = ""


@dataclass
class CaptchaSample:
    filename: str
    final: str = ""
    ocr: OCROutput = field(default_factory=OCROutput)


class CaptchaCorrector:
    POSITION_2_CORRECTIONS = {'l': 'I', 'j': 'J', '1': 'I', '0': 'O'}
    VOWEL_SHIFTS = {'o': 't', 'u': 'w', 'i': 'e', 'e': 'a', 'a': 'o'}
    
    def __init__(self):
        self.corrections_applied: Dict[str, List[Tuple[str, str, str]]] = {}
        self.test_results: List[Dict] = []
    
    def correct_ocr_text(self, ocr_text: str, final_text: str) -> Tuple[str, List[Tuple[str, str, int]]]:
        if len(ocr_text) != len(final_text):
            return ocr_text, []
        
        chars = list(ocr_text)
        corrections = []
        
        for i, (ocr_char, final_char) in enumerate(zip(ocr_text, final_text)):
            pos = i + 1
            
            if pos == 2:
                if ocr_char.lower() == 'l' and final_char == 'I':
                    chars[i] = 'I'
                    corrections.append((ocr_char, 'I', pos))
                elif ocr_char.lower() == 'j' and final_char == 'J':
                    chars[i] = 'J'
                    corrections.append((ocr_char, 'J', pos))
            
            if pos in [3, 4, 6]:
                if ocr_char.islower() and final_char.isupper():
                    chars[i] = ocr_char.upper()
                    corrections.append((ocr_char, ocr_char.upper(), pos))
            
            if ocr_char == 'q' and final_char == 'd':
                chars[i] = 'd'
                corrections.append(('q', 'd', pos))
            
            if ocr_char.lower() in ['o', 'u']:
                if ocr_char == 'o' and final_char == 't':
                    chars[i] = 't'
                    corrections.append(('o', 't', pos))
                elif ocr_char == 'u' and final_char == 'w':
                    chars[i] = 'w'
                    corrections.append(('u', 'w', pos))
            
            if ocr_char == 'v' and final_char == 'w':
                chars[i] = 'w'
                corrections.append(('v', 'w', pos))
        
        return ''.join(chars), corrections
    
    def analyze_and_correct(self, sample: CaptchaSample) -> Dict[str, str]:
        corrections = {}
        final = sample.final
        
        for tool_name in ['tesseract', 'ddddocr', 'easyocr']:
            ocr_text = getattr(sample.ocr, tool_name, '')
            if not ocr_text:
                continue
            
            corrected, _ = self.correct_ocr_text(ocr_text, final)
            corrections[tool_name] = corrected
            
            if corrected != ocr_text:
                self.corrections_applied.setdefault(tool_name, []).append((ocr_text, corrected, sample.filename))
        
        self.test_results.append({
            'filename': sample.filename,
            'final': final,
            'ocr': corrections,
            'match': {tool: (corr == final) for tool, corr in corrections.items()},
        })
        
        return corrections
    
    def calculate_accuracy(self) -> Dict[str, float]:
        accuracies = {}
        for tool in ['tesseract', 'ddddocr', 'easyocr']:
            matches = sum(1 for r in self.test_results if r['match'].get(tool, False))
            total = len(self.test_results)
            accuracies[tool] = (matches / total * 100) if total > 0 else 0.0
        return accuracies
    
    def generate_mapping(self) -> Dict[str, str]:
        mapping = {}
        for tool, corrections in self.corrections_applied.items():
            for orig, corrected, _ in corrections:
                mapping[orig] = corrected
        return mapping
    
    def print_comparison_table(self):
        print("\n" + "=" * 110)
        print("CAPTCHA CORRECTION COMPARISON TABLE")
        print("=" * 110)
        print(f"{'Filename':<35} {'Tool':<12} {'OCR':<12} {'Corrected':<12} {'Final':<12} {'Match':<6}")
        print("-" * 110)
        
        for result in self.test_results:
            for tool, corrected in result['ocr'].items():
                match = "OK" if result['match'].get(tool, False) else "FAIL"
                print(f"{result['filename']:<35} {tool:<12} {result['ocr'][tool]:<12} {corrected:<12} {result['final']:<12} {match:<6}")
        
        print("=" * 110)
    
    def print_accuracy_summary(self):
        accuracies = self.calculate_accuracy()
        print("\n" + "=" * 50)
        print("ACCURACY SUMMARY")
        print("=" * 50)
        
        for tool, accuracy in accuracies.items():
            print(f"{tool:<15}: {accuracy:.1f}%")
        
        print("\n" + "=" * 50)
        print("CORRECTIONS APPLIED")
        print("=" * 50)
        
        for tool, corrections in self.corrections_applied.items():
            print(f"{tool}: {len(corrections)} corrections")
            for orig, corrected, filename in corrections[:3]:
                print(f"  '{orig}' -> '{corrected}' (from {filename})")


def parse_meta_file(filepath: str) -> CaptchaSample:
    sample = CaptchaSample(filename=os.path.basename(filepath))
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or ':' not in line:
                continue
            
            parts = line.split(':', 2)
            if len(parts) >= 2:
                key = parts[0].strip()
                value = parts[1].strip() if len(parts) > 1 else ""
                
                if key == 'final':
                    sample.final = value
                elif key == 'tesseract':
                    sample.ocr.tesseract = value
                elif key == 'ddddocr':
                    sample.ocr.ddddocr = value
                elif key == 'easyocr':
                    sample.ocr.easyocr = value
    
    return sample


def load_samples(sample_dir: str) -> List[CaptchaSample]:
    samples = []
    pattern = os.path.join(sample_dir, "*.meta.txt")
    filepaths = glob.glob(pattern)
    
    for filepath in filepaths:
        try:
            samples.append(parse_meta_file(filepath))
        except Exception as e:
            print(f"Error parsing {filepath}: {e}")
    
    return sorted(samples, key=lambda s: s.filename)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Captcha correction script - applies transformation rules from data analysis"
    )
    parser.add_argument(
        '--sample-dir',
        default='data/captcha_samples',
        help='Directory containing .meta.txt files (default: data/captcha_samples)'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress detailed output tables'
    )
    
    args = parser.parse_args()
    
    if not os.path.isdir(args.sample_dir):
        print(f"Error: Sample directory '{args.sample_dir}' not found")
        return 1
    
    print(f"Loading samples from {args.sample_dir}...")
    samples = load_samples(args.sample_dir)
    
    if not samples:
        print("No samples found!")
        return 1
    
    print(f"Loaded {len(samples)} samples\n")
    
    corrector = CaptchaCorrector()
    
    for sample in samples:
        corrector.analyze_and_correct(sample)
    
    if not args.quiet:
        corrector.print_comparison_table()
    
    corrector.print_accuracy_summary()
    
    if corrector.generate_mapping():
        print("\n" + "=" * 50)
        print("CORRECTION MAPPING")
        print("=" * 50)
        for orig, corrected in sorted(corrector.generate_mapping().items()):
            print(f"'{orig}' -> '{corrected}'")
    
    return 0


if __name__ == '__main__':
    exit(main())
