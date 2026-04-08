#!/bin/bash
echo "Installing dependencies..."
pip install -r requirements.txt
echo ""
echo "Starting NoteCraft on http://localhost:5000"
echo "Press Ctrl+C to stop."
echo ""
python app.py
