#!/bin/bash
# Converts videos already in Downloads/VDM so they play in QuickTime / Preview
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade -r requirements.txt
python app.py --fix-old
