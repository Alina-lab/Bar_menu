#!/usr/bin/env python3
"""Совместимая команда: текущая авторская модель 60 л, без старой оптимизации."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.economics import load_inputs, run

def load_author_menu():
    return load_inputs()[0]

if __name__ == "__main__":
    result=run()
    print("Закупка сырья:",result["purchase_cost_rub"],"₽")
