# NoteCraft - Flask Edition

A fully local developer toolkit built with Flask. No CORS issues, real HTTP proxy, full SSL control.

## Features
- 📓 **Notes** — Notebooks + pages, rename/delete via context menu, save as .txt, export notebook
- ✨ **Beautifier** — JSON & XML beautify/minify with syntax highlighting
- ⚙ **XSD Generator** — SAP CPI-compatible XSD from XML or JSON, namespace toggle
- ◈ **Payload Comparator** — JSON & XML diff with **line highlighting** in left/right panels
- ⚡ **API Tester** — Full Postman-equivalent (headers, auth, body types, multi-send, saved requests, SSL toggle)
- ☁ **Solace Tester** — Publish to Solace Queue/Topic via REST Delivery Protocol

## Setup & Run

### Windows
```
Double-click run.bat
```

### Linux / Mac
```bash
chmod +x run.sh
./run.sh
```

### Manual
```bash
pip install -r requirements.txt
python app.py
```

Then open: **http://localhost:5000**

## Why Flask?
- **No CORS errors** — all API calls go through the Flask backend proxy
- **SSL verify toggle** — disables SSL verification server-side (works on corporate networks)
- **Real XML diff** — server-side XML parsing handles namespaces, attributes, repeated elements correctly

## Notes
- All state is saved in your browser's localStorage automatically
- Solace publishing uses REST Delivery Protocol (HTTP POST). Ensure REST port (default 9000) is enabled on your broker.
- For AMQP/SMF, use SAP CPI AMQP adapter or a native Solace client.
