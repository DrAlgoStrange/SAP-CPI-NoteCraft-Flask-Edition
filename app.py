from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
import requests
import urllib3
import re
import traceback
import threading
import time
import uuid
import os

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
# SCHEDULER STORE (in-memory, per server session)
# ─────────────────────────────────────────────
schedulers = {}  # id -> {thread, stop_event, logs, status, ...}

def _run_scheduler(sched_id, req_data, interval_sec, max_sec):
    entry = schedulers[sched_id]
    stop_ev = entry['stop_event']
    start_t = time.time()
    run_num = 0
    while not stop_ev.is_set():
        elapsed = time.time() - start_t
        if elapsed >= max_sec:
            entry['status'] = 'done'
            entry['logs'].append({'t': _ts(), 'msg': f'Max time reached ({max_sec}s). Scheduler stopped.', 'ok': True})
            break
        run_num += 1
        try:
            url = req_data.get('url','')
            method = req_data.get('method','GET').upper()
            headers = req_data.get('headers', {})
            body = req_data.get('body', None)
            ssl_verify = req_data.get('sslVerify', True)
            if not ssl_verify:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            t0 = time.time()
            resp = requests.request(method=method, url=url, headers=headers,
                data=body if isinstance(body,str) else None,
                json=body if isinstance(body,dict) else None,
                verify=ssl_verify, timeout=30, allow_redirects=True)
            ms = round((time.time()-t0)*1000)
            entry['logs'].append({'t':_ts(),'msg':f'#{run_num} → {resp.status_code} {resp.reason} ({ms}ms)','ok':resp.ok})
        except Exception as ex:
            entry['logs'].append({'t':_ts(),'msg':f'#{run_num} → ERROR: {str(ex)}','ok':False})
        # Trim log to last 200 entries
        if len(entry['logs']) > 200:
            entry['logs'] = entry['logs'][-200:]
        # Wait for next interval (check stop every second)
        for _ in range(int(interval_sec)):
            if stop_ev.is_set(): break
            time.sleep(1)
    entry['status'] = entry['status'] if entry['status']=='done' else 'stopped'

def _ts():
    return time.strftime('%H:%M:%S')

@app.route('/api/scheduler/start', methods=['POST'])
def scheduler_start():
    data = request.json
    interval_sec = max(5, int(data.get('interval', 60)))
    max_sec = max(interval_sec, int(data.get('maxTime', 3600)))
    req_data = data.get('request', {})
    sched_id = str(uuid.uuid4())[:8]
    stop_ev = threading.Event()
    entry = {'status':'running','logs':[],'stop_event':stop_ev,'interval':interval_sec,'maxTime':max_sec}
    schedulers[sched_id] = entry
    t = threading.Thread(target=_run_scheduler, args=(sched_id, req_data, interval_sec, max_sec), daemon=True)
    entry['thread'] = t
    t.start()
    entry['logs'].append({'t':_ts(),'msg':f'Scheduler started — every {interval_sec}s, max {max_sec}s','ok':True})
    return jsonify({'success':True,'id':sched_id})

@app.route('/api/scheduler/stop', methods=['POST'])
def scheduler_stop():
    sched_id = request.json.get('id','')
    entry = schedulers.get(sched_id)
    if not entry:
        return jsonify({'success':False,'error':'Not found'})
    entry['stop_event'].set()
    entry['status'] = 'stopping'
    return jsonify({'success':True})

@app.route('/api/scheduler/status', methods=['POST'])
def scheduler_status():
    sched_id = request.json.get('id','')
    entry = schedulers.get(sched_id)
    if not entry:
        return jsonify({'success':False,'error':'Not found'})
    return jsonify({'success':True,'status':entry['status'],'logs':entry['logs'][-50:]})

# ─────────────────────────────────────────────
# SERVE FRONTEND
# ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ─────────────────────────────────────────────
# JSON / XML BEAUTIFIER
# ─────────────────────────────────────────────
@app.route('/api/beautify', methods=['POST'])
def beautify():
    data = request.json
    content = data.get('content', '').strip()
    mode = data.get('mode', 'json')
    try:
        if mode == 'json':
            obj = json.loads(content)
            result = json.dumps(obj, indent=2, ensure_ascii=False)
        else:
            # XML beautify
            root = ET.fromstring(content)
            raw = ET.tostring(root, encoding='unicode')
            pretty = minidom.parseString(raw).toprettyxml(indent='  ')
            # remove extra blank lines minidom adds
            lines = [l for l in pretty.split('\n') if l.strip()]
            result = '\n'.join(lines)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# XSD GENERATOR
# ─────────────────────────────────────────────
def infer_xs_type(value):
    v = str(value).strip()
    if not v:
        return 'xs:string'
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', v):
        return 'xs:dateTime'
    if re.match(r'^\d{4}-\d{2}-\d{2}$', v):
        return 'xs:date'
    if re.match(r'^-?\d+$', v):
        return 'xs:integer'
    if re.match(r'^-?\d+\.\d+$', v):
        return 'xs:decimal'
    if v.lower() in ('true', 'false'):
        return 'xs:boolean'
    return 'xs:string'

def xml_node_to_xsd(node, indent, all_required, unbounded, use_ns):
    pad = '  ' * indent
    children = list(node)
    attrs = node.attrib
    tag = node.tag
    # Strip namespace from tag for display
    if '{' in tag:
        tag = tag.split('}', 1)[1]

    min_occ = '' if all_required else ' minOccurs="0"'
    use_attr = 'required' if all_required else 'optional'

    if not children and not attrs:
        xtype = infer_xs_type(node.text or '')
        return f'{pad}<xs:element name="{tag}" type="{xtype}"{min_occ}/>'

    # Track child tag frequencies
    child_tags = {}
    for c in children:
        ctag = c.tag.split('}', 1)[1] if '{' in c.tag else c.tag
        child_tags[ctag] = child_tags.get(ctag, 0) + 1

    lines = [f'{pad}<xs:element name="{tag}"{min_occ}>',
             f'{pad}  <xs:complexType>']
    if children:
        lines.append(f'{pad}    <xs:sequence>')
        seen = set()
        for child in children:
            ctag = child.tag.split('}', 1)[1] if '{' in child.tag else child.tag
            if ctag in seen:
                continue
            seen.add(ctag)
            cnt = child_tags[ctag]
            max_o = ' maxOccurs="unbounded"' if (unbounded and cnt > 1) else (f' maxOccurs="{cnt}"' if cnt > 1 else '')
            child_xsd = xml_node_to_xsd(child, indent + 3, all_required, unbounded, use_ns)
            # inject maxOccurs into the element tag if needed
            if max_o:
                child_xsd = child_xsd.replace(f'name="{ctag}"', f'name="{ctag}"{max_o}', 1)
            lines.append(child_xsd)
        lines.append(f'{pad}    </xs:sequence>')
    for aname in attrs:
        aname_clean = aname.split('}', 1)[1] if '{' in aname else aname
        lines.append(f'{pad}    <xs:attribute name="{aname_clean}" type="xs:string" use="{use_attr}"/>')
    lines += [f'{pad}  </xs:complexType>', f'{pad}</xs:element>']
    return '\n'.join(lines)

def json_val_to_xsd(name, value, indent, all_required, unbounded, use_ns):
    pad = '  ' * indent
    min_occ = '' if all_required else ' minOccurs="0"'

    def js_type(v):
        if v is None: return 'xs:string'
        if isinstance(v, bool): return 'xs:boolean'
        if isinstance(v, int): return 'xs:integer'
        if isinstance(v, float): return 'xs:decimal'
        if isinstance(v, str):
            if re.match(r'^\d{4}-\d{2}-\d{2}T', v): return 'xs:dateTime'
            if re.match(r'^\d{4}-\d{2}-\d{2}$', v): return 'xs:date'
        return 'xs:string'

    if isinstance(value, list):
        max_o = ' maxOccurs="unbounded"'
        sample = value[0] if value else None
        if sample is None or not isinstance(sample, dict):
            t = js_type(sample)
            return f'{pad}<xs:element name="{name}" type="{t}"{min_occ}{max_o}/>'
        lines = [f'{pad}<xs:element name="{name}"{min_occ}{max_o}>',
                 f'{pad}  <xs:complexType>', f'{pad}    <xs:sequence>']
        for k, v in sample.items():
            lines.append(json_val_to_xsd(k, v, indent + 3, all_required, unbounded, use_ns))
        lines += [f'{pad}    </xs:sequence>', f'{pad}  </xs:complexType>', f'{pad}</xs:element>']
        return '\n'.join(lines)

    if isinstance(value, dict):
        lines = [f'{pad}<xs:element name="{name}"{min_occ}>',
                 f'{pad}  <xs:complexType>', f'{pad}    <xs:sequence>']
        for k, v in value.items():
            lines.append(json_val_to_xsd(k, v, indent + 3, all_required, unbounded, use_ns))
        lines += [f'{pad}    </xs:sequence>', f'{pad}  </xs:complexType>', f'{pad}</xs:element>']
        return '\n'.join(lines)

    return f'{pad}<xs:element name="{name}" type="{js_type(value)}"{min_occ}/>'

@app.route('/api/generate-xsd', methods=['POST'])
def generate_xsd():
    data = request.json
    content = data.get('content', '').strip()
    namespace = data.get('namespace', 'http://www.example.com/schema').strip()
    all_required = data.get('allRequired', True)
    unbounded = data.get('unbounded', False)
    use_ns = data.get('useNamespace', True)

    try:
        body_lines = []

        if content.startswith('<') or content.startswith('<?'):
            # XML input
            root = ET.fromstring(content)
            body_lines.append(xml_node_to_xsd(root, 1, all_required, unbounded, use_ns))
        else:
            # JSON input
            obj = json.loads(content)
            if isinstance(obj, dict):
                keys = list(obj.keys())
                if len(keys) == 1:
                    root_name, root_val = keys[0], obj[keys[0]]
                else:
                    root_name, root_val = 'Root', obj
            else:
                root_name, root_val = 'Root', obj
            body_lines.append(json_val_to_xsd(root_name, root_val, 1, all_required, unbounded, use_ns))

        # Build XSD header
        if use_ns and namespace:
            header = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"\n'
                f'           targetNamespace="{namespace}"\n'
                f'           xmlns:tns="{namespace}"\n'
                '           elementFormDefault="qualified">\n'
            )
        else:
            header = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"\n'
                '           elementFormDefault="unqualified">\n'
            )

        xsd = header + '\n' + '\n'.join(body_lines) + '\n\n</xs:schema>'
        return jsonify({'success': True, 'xsd': xsd})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# JSON ↔ XML CONVERTER
# ─────────────────────────────────────────────
import re as _re

def _xml_escape(s):
    """Escape special XML characters in text content."""
    return (str(s)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))

def _safe_tag(s):
    """Ensure a string is a valid XML tag name."""
    s = _re.sub(r'[^\w\-.]', '_', str(s))
    if not s or s[0].isdigit() or s[0] in ('-', '.'):
        s = '_' + s
    return s or '_element'

def _json_to_xml_lines(data, tag, depth=0):
    """
    Build XML lines from JSON data using pure string construction
    (avoids xml.etree.ElementTree which mangles tags like 'name' -> 'n').

    Arrays are expanded as repeated sibling elements with the same tag name.
    """
    pad = '    ' * depth
    safe = _safe_tag(tag)
    lines = []

    if isinstance(data, list):
        # Emit each list item as a repeated <safe> sibling
        for item in data:
            lines.extend(_json_to_xml_lines(item, tag, depth))
        return lines

    if isinstance(data, dict):
        lines.append(pad + '<' + safe + '>')
        for key, val in data.items():
            if isinstance(val, list):
                for item in val:
                    lines.extend(_json_to_xml_lines(item, key, depth + 1))
            else:
                lines.extend(_json_to_xml_lines(val, key, depth + 1))
        lines.append(pad + '</' + safe + '>')
    else:
        text = _xml_escape('' if data is None else data)
        lines.append(pad + '<' + safe + '>' + text + '</' + safe + '>')

    return lines

def _xml_node_to_json(node):
    """Recursively convert an ElementTree node to a Python dict."""
    children = list(node)
    if not children:
        return (node.text or '').strip() or None
    result = {}
    child_tags = {}
    for child in children:
        tag = child.tag.split('}', 1)[1] if '{' in child.tag else child.tag
        child_tags[tag] = child_tags.get(tag, 0) + 1
    for child in children:
        tag = child.tag.split('}', 1)[1] if '{' in child.tag else child.tag
        val = _xml_node_to_json(child)
        if child_tags[tag] > 1:
            if tag not in result:
                result[tag] = []
            result[tag].append(val)
        else:
            result[tag] = val
    return result

@app.route('/api/convert', methods=['POST'])
def convert():
    data = request.json
    direction = data.get('direction', 'json2xml')
    content = data.get('content', '').strip()
    root_name = (data.get('rootName', '') or 'root').strip() or 'root'

    try:
        if direction == 'json2xml':
            obj = json.loads(content)
            lines = ['<?xml version="1.0" encoding="UTF-8"?>']
            lines.extend(_json_to_xml_lines(obj, root_name, 0))
            result = '\n'.join(lines)
        else:
            # xml2json — ET only reads here, no tag mangling on read
            root_el = ET.fromstring(content)
            tag = root_el.tag.split('}', 1)[1] if '{' in root_el.tag else root_el.tag
            obj = {tag: _xml_node_to_json(root_el)}
            result = json.dumps(obj, indent=2, ensure_ascii=False)

        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# PAYLOAD COMPARATOR
# ─────────────────────────────────────────────
def flatten_json(obj, prefix=''):
    result = {}
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            sub = flatten_json(v, f'{prefix}[{i}]')
            result.update(sub)
        if not obj:
            result[prefix] = []
    elif isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f'{prefix}.{k}' if prefix else k
            sub = flatten_json(v, new_key)
            result.update(sub)
        if not obj:
            result[prefix] = {}
    else:
        result[prefix] = obj
    return result

def flatten_xml_node(node, prefix=''):
    result = {}
    tag = node.tag
    if '{' in tag:
        tag = tag.split('}', 1)[1]
    key = f'{prefix}.{tag}' if prefix else tag

    # Attributes
    for aname, aval in node.attrib.items():
        aname_clean = aname.split('}', 1)[1] if '{' in aname else aname
        result[f'{key}@{aname_clean}'] = aval

    children = list(node)
    if not children:
        result[key] = (node.text or '').strip()
    else:
        # Count child tag frequencies
        child_counts = {}
        for c in children:
            ctag = c.tag.split('}', 1)[1] if '{' in c.tag else c.tag
            child_counts[ctag] = child_counts.get(ctag, 0) + 1
        child_idx = {}
        for child in children:
            ctag = child.tag.split('}', 1)[1] if '{' in child.tag else child.tag
            idx = child_idx.get(ctag, 0)
            child_key = f'{key}.{ctag}[{idx}]' if child_counts[ctag] > 1 else f'{key}.{ctag}'
            result.update(flatten_xml_node(child, key))
            # Override with indexed key for repeated elements
            if child_counts[ctag] > 1:
                sub = flatten_xml_node(child, key)
                # Re-key with index
                for sk, sv in sub.items():
                    new_sk = sk.replace(f'{key}.{ctag}', f'{key}.{ctag}[{idx}]', 1)
                    result[new_sk] = sv
                    # Remove non-indexed duplicate if exists
                    if f'{key}.{ctag}' in result and f'{key}.{ctag}[0]' in result:
                        del result[f'{key}.{ctag}']
            child_idx[ctag] = idx + 1
    return result

@app.route('/api/compare', methods=['POST'])
def compare():
    data = request.json
    mode = data.get('mode', 'json')
    left_txt = data.get('left', '').strip()
    right_txt = data.get('right', '').strip()

    try:
        if mode == 'json':
            left_obj = json.loads(left_txt)
            right_obj = json.loads(right_txt)
            flat_l = flatten_json(left_obj)
            flat_r = flatten_json(right_obj)
        else:
            left_root = ET.fromstring(left_txt)
            right_root = ET.fromstring(right_txt)
            flat_l = flatten_xml_node(left_root)
            flat_r = flatten_xml_node(right_root)

        all_keys = set(list(flat_l.keys()) + list(flat_r.keys()))
        missing_in_right = []
        missing_in_left = []
        mismatches = []

        for k in sorted(all_keys):
            in_l = k in flat_l
            in_r = k in flat_r
            if in_l and not in_r:
                missing_in_right.append(k)
            elif not in_l and in_r:
                missing_in_left.append(k)
            elif str(flat_l[k]) != str(flat_r[k]):
                mismatches.append({'key': k, 'left': str(flat_l[k]), 'right': str(flat_r[k])})

        return jsonify({
            'success': True,
            'missingInRight': missing_in_right,
            'missingInLeft': missing_in_left,
            'mismatches': mismatches,
            'flatLeft': flat_l,
            'flatRight': flat_r
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# API PROXY (avoids CORS, handles SSL verify)
# ─────────────────────────────────────────────
@app.route('/api/proxy', methods=['POST'])
def proxy():
    data = request.json
    url = data.get('url', '')
    method = data.get('method', 'GET').upper()
    headers = data.get('headers', {})
    body = data.get('body', None)
    ssl_verify = data.get('sslVerify', True)
    timeout = data.get('timeout', 30)

    if not ssl_verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:
        import time
        start = time.time()
        resp = requests.request(
            method=method,
            url=url,
            headers=headers,
            data=body if isinstance(body, str) else None,
            json=body if isinstance(body, dict) else None,
            verify=ssl_verify,
            timeout=timeout,
            allow_redirects=True
        )
        elapsed = round((time.time() - start) * 1000)

        resp_headers = dict(resp.headers)
        try:
            body_text = resp.text
        except:
            body_text = ''

        return jsonify({
            'success': True,
            'status': resp.status_code,
            'statusText': resp.reason,
            'headers': resp_headers,
            'body': body_text,
            'time': elapsed,
            'size': len(resp.content)
        })
    except requests.exceptions.SSLError as e:
        return jsonify({'success': False, 'error': f'SSL Error: {str(e)}. Try disabling SSL verification.'})
    except requests.exceptions.ConnectionError as e:
        return jsonify({'success': False, 'error': f'Connection Error: {str(e)}'})
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': f'Request timed out after {timeout}s'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# SOLACE PUBLISHER PROXY
# ─────────────────────────────────────────────
@app.route('/api/solace/publish', methods=['POST'])
def solace_publish():
    data = request.json
    host = data.get('host', '').strip()
    vpn = data.get('vpn', '')
    user = data.get('user', '')
    password = data.get('pass', '')
    dest_type = data.get('type', 'queue')
    dest = data.get('dest', '')
    body = data.get('body', '')
    content_type = data.get('contentType', 'application/json')
    ssl_verify = data.get('sslVerify', True)

    if not host or not dest:
        return jsonify({'success': False, 'error': 'Host and destination required'})

    # Normalize host
    if not host.startswith('http'):
        host = 'http://' + host

    # Build REST URL (Solace REST port default 9000)
    base = re.sub(r':\d+$', ':9000', host)
    if dest_type == 'queue':
        url = f'{base}/QUEUE/{requests.utils.quote(dest, safe="")}'
    else:
        url = f'{base}/TOPIC/{requests.utils.quote(dest, safe="")}'

    try:
        import time
        start = time.time()
        headers = {'Content-Type': content_type}
        resp = requests.post(
            url,
            data=body,
            headers=headers,
            auth=(user, password) if user else None,
            verify=ssl_verify,
            timeout=15
        )
        elapsed = round((time.time() - start) * 1000)
        return jsonify({
            'success': resp.ok,
            'status': resp.status_code,
            'time': elapsed,
            'url': url,
            'message': f'HTTP {resp.status_code} - {resp.reason}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'url': url})

# ─────────────────────────────────────────────
# GROOVY / SAP CPI SCRIPT SIMULATOR
# Spawns a real `groovy` subprocess with a full
# mock CPI Message API harness (CpiRunner.groovy)
# ─────────────────────────────────────────────
import subprocess, tempfile, shutil, platform

def _groovy_binary():
    """Return path to groovy executable, or None if not found."""
    # Check PATH first
    g = shutil.which('groovy')
    if g:
        return g
    # Common Windows install locations
    if platform.system() == 'Windows':
        candidates = [
            r'C:\groovy\bin\groovy.bat',
            r'C:\Program Files\Groovy\bin\groovy.bat',
            r'C:\tools\groovy\bin\groovy.bat',
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    return None

def _runner_path():
    """Absolute path to CpiRunner.groovy, handles spaces."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, 'groovy_runner', 'CpiRunner.groovy')

@app.route('/api/groovy/check', methods=['GET'])
def groovy_check():
    g = _groovy_binary()
    runner = _runner_path()
    return jsonify({
        'groovyFound'  : g is not None,
        'groovyPath'   : g or '',
        'runnerFound'  : os.path.isfile(runner),
        'runnerPath'   : runner,
    })

@app.route('/api/groovy/execute', methods=['POST'])
def groovy_execute():
    data        = request.json
    script      = data.get('script', '')
    fn_name     = (data.get('function') or 'processData').strip()
    body        = data.get('body', '')
    headers     = data.get('headers', {})
    properties  = data.get('properties', {})

    # ── Locate groovy ────────────────────────────────────────────
    groovy_bin = _groovy_binary()
    if not groovy_bin:
        return jsonify({
            'success': False,
            'error'  : (
                'Groovy not found on PATH.\n'
                'Install it with one of:\n'
                '  • choco install groovy          (Windows, requires Chocolatey)\n'
                '  • sdk install groovy            (SDKMAN — Windows WSL / Linux / Mac)\n'
                '  • sudo apt-get install groovy   (Ubuntu/Debian)\n'
                '  • brew install groovy           (macOS)\n\n'
                'After install, restart the Flask app.'
            ),
            'console': []
        })

    # ── Locate CpiRunner.groovy ──────────────────────────────────
    runner = _runner_path()
    if not os.path.isfile(runner):
        return jsonify({
            'success': False,
            'error'  : (
                f'CpiRunner.groovy not found at:\n{runner}\n\n'
                'Make sure the groovy_runner\ folder is inside your notecraft_flask\ project folder.'
            ),
            'console': []
        })

    payload = {
        'script'    : script,
        'function'  : fn_name,
        'body'      : body,
        'headers'   : headers,
        'properties': properties,
    }

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            in_file  = os.path.join(tmpdir, 'cpi_input.json')
            out_file = os.path.join(tmpdir, 'cpi_output.json')

            with open(in_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)

            # Build command — quote each arg so spaces in paths work
            cmd = [groovy_bin, runner, in_file, out_file]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=tmpdir          # run from temp dir, not from path-with-spaces
            )

            # ── Parse output ────────────────────────────────────
            if os.path.isfile(out_file):
                with open(out_file, 'r', encoding='utf-8') as f:
                    result = json.load(f)
                # Attach any groovy stderr (compilation warnings etc.) to console
                if proc.stderr.strip():
                    result.setdefault('console', [])
                    stderr_lines = [l for l in proc.stderr.strip().splitlines()
                                    if l.strip() and 'WARNING' not in l]
                    if stderr_lines:
                        result['console'] = stderr_lines + result['console']
                return jsonify(result)
            else:
                # Runner crashed before writing output
                stderr = proc.stderr.strip() or proc.stdout.strip()
                # Clean up Groovy stack trace — keep first meaningful lines
                lines = stderr.splitlines()
                clean = []
                for l in lines:
                    if any(skip in l for skip in [
                        'org.codehaus', 'sun.reflect', 'java.lang.reflect',
                        'groovy.lang.Meta', 'at org.', 'at java.', 'at sun.',
                        'at com.sun', 'at groovy.ui'
                    ]):
                        continue
                    clean.append(l)
                friendly = '\n'.join(clean).strip() or 'Groovy runner crashed with no output.'
                return jsonify({'success': False, 'error': friendly, 'console': []})

    except subprocess.TimeoutExpired:
        return jsonify({'success': False,
                        'error': 'Script execution timed out after 60 seconds.',
                        'console': []})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'console': []})

# ─────────────────────────────────────────────
# FILE-BASED STORAGE (replaces localStorage)
# ─────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

@app.route('/api/storage/save', methods=['POST'])
def storage_save():
    data = request.json
    key = data.get('key', '')
    value = data.get('value')
    if not key or not re.match(r'^[\w\-]+$', key):
        return jsonify({'success': False, 'error': 'Invalid key'})
    path = os.path.join(DATA_DIR, key + '.json')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/storage/load', methods=['POST'])
def storage_load():
    key = request.json.get('key', '')
    if not key or not re.match(r'^[\w\-]+$', key):
        return jsonify({'success': False, 'error': 'Invalid key'})
    path = os.path.join(DATA_DIR, key + '.json')
    if not os.path.isfile(path):
        return jsonify({'success': True, 'value': None})
    try:
        with open(path, 'r', encoding='utf-8') as f:
            value = json.load(f)
        return jsonify({'success': True, 'value': value})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True, port=5000)