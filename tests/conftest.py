# -*- coding: utf-8 -*-
"""Ajoute la racine du projet au sys.path — permet `import modules.xxx` quel que soit
l'endroit d'où pytest est lancé (même mécanisme que main.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
