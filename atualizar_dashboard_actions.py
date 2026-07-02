#!/usr/bin/env python3
"""
Script para rodar no GitHub Actions.
Lê dados do Google Sheets e atualiza o index.html no repositório.
"""

import sys, os, re, json, base64
from datetime import datetime

try:
    import pandas as pd
    import gspread
    from google.oauth2.service_account import Credentials
    import requests
except ImportError as e:
    print(f"❌ Dependência não instalada: {e}")
    sys.exit(1)

# ── Configurações via variáveis de ambiente (definidas nos Secrets do GitHub) ──
GITHUB_TOKEN = os.environ.get('PAT_TOKEN', '')        # Secret: PAT_TOKEN
GITHUB_REPO  = "victorvargas-prog/mude-dooh"
SHEET_ID     = "1HZ0g4dKn5OmaXZV75_dNsVY25dcQF6lgZqxv6Sb84qA"


# ────────────────────────────────────────────
def connect_sheets():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS', '')
    if not creds_json:
        print("❌ GOOGLE_CREDENTIALS não definido nos Secrets.")
        sys.exit(1)
    try:
        creds_dict = json.loads(creds_json)
    except json.JSONDecodeError:
        print("❌ GOOGLE_CREDENTIALS não é um JSON válido.")
        sys.exit(1)

    scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly',
              'https://www.googleapis.com/auth/drive.readonly']
    creds  = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(SHEET_ID)
    print(f"✓ Conectado: {sheet.title}")
    return sheet


# ────────────────────────────────────────────
def ws_to_df(worksheet):
    values = worksheet.get_all_values()
    return pd.DataFrame(values)


def parse_date(v):
    try:
        return pd.to_datetime(v, dayfirst=True, errors='coerce')
    except Exception:
        return pd.NaT


def parse_num(v):
    """Parse number from string, handling % and Brazilian comma."""
    try:
        if v in ('', None): return None
        s = str(v).strip()
        if not s: return None
        is_pct = '%' in s
        s = s.replace('%','').replace(' ','')
        if ',' in s and '.' in s:
            s = s.replace('.','').replace(',','.')
        elif ',' in s:
            s = s.replace(',','.')
        num = float(s)
        if is_pct: num = num / 100.0
        return num
    except (ValueError, TypeError):
        return None


# ────────────────────────────────────────────
def extract_data(sheet):
    today = pd.Timestamp('today').normalize()
    print(f"📅 Data de referência: {today.strftime('%d/%m/%Y')}")

    # ── Gerencial_TX ──
    ws_g  = sheet.worksheet('Gerencial_TX')
    df_g  = ws_to_df(ws_g)
    dates = df_g.iloc[3, :].apply(parse_date)
    all_valid  = sorted([(i,d) for i,d in enumerate(dates) if pd.notna(d)], key=lambda x: x[1])
    past_valid = [(i,d) for i,d in all_valid if d <= today]
    if not past_valid:
        print("❌ Nenhuma data válida.")
        sys.exit(1)
    last_col, last_date = past_valid[-1]
    print(f"  Última semana: {last_date.strftime('%d/%m/%Y')}")

    def gv(df, row, col):
        try:
            if row >= df.shape[0] or col >= df.shape[1]: return None
            return parse_num(df.iloc[row, col])
        except Exception: return None

    city_rows    = {'Rio de Janeiro':[5,6],'Recife':[8,9],'Brasília':[10],'Florianópolis':[11],'Fortaleza':[12]}
    circuit_rows = {'RJ — ORLA 1':5,'RJ — ORLA 2':6,'Recife — Urbano':8,'Recife — Orla':9,
                    'Circuito BSB':10,'Circuito FLN':11,'Circuito FOR':12}

    occ = {}
    for city, rows in city_rows.items():
        vals = [min(gv(df_g, r, last_col), 1.0) for r in rows if gv(df_g, r, last_col) is not None]
        occ[city] = round(sum(vals)/len(vals), 4) if vals else 0

    def build_series(row_map):
        result = {}
        for key, rows in row_map.items():
            if isinstance(rows, int): rows = [rows]
            pts = []
            for col, d in all_valid:
                vals = [min(gv(df_g, r, col), 1.0) for r in rows if gv(df_g, r, col) is not None]
                pts.append([d.strftime('%d/%m/%Y'), round(sum(vals)/len(vals), 4) if vals else None])
            result[key] = pts
        return result

    weekly   = build_series(city_rows)
    circuits = build_series(circuit_rows)

    # RJ gap corrections
    gap = {'24/02/2026':0.9550,'03/03/2026':0.9200,'10/03/2026':0.9550,'17/03/2026':0.7800,
           '24/03/2026':0.7700,'31/03/2026':0.7300,'07/04/2026':0.8000,'14/04/2026':0.7650}
    for pt in weekly['Rio de Janeiro']:
        if pt[0] in gap and pt[1] is None:
            pt[1] = gap[pt[0]]

    # ── Active campaigns ──
    sheets_map = {'Rio de Janeiro':'Rio de Janeiro','Recife':'Recife','Brasília':'Brasilia',
                  'Florianópolis':'Florianópolis','Fortaleza':'Fortaleza'}
    all_camps = {}
    for city, ws_name in sheets_map.items():
        ws = sheet.worksheet(ws_name)
        df = ws_to_df(ws)
        camps = []
        for col in range(df.shape[1]):
            name = df.iloc[0, col] if df.shape[0] > 0 else ''
            if not isinstance(name, str) or len(name.strip()) < 2: continue
            inicio = parse_date(df.iloc[1, col] if df.shape[0] > 1 else '')
            fim    = parse_date(df.iloc[2, col] if df.shape[0] > 2 else '')
            tipo   = df.iloc[3, col] if df.shape[0] > 3 else ''
            if pd.notna(inicio) and pd.notna(fim) and inicio <= today <= fim:
                faces = sum(1 for r in range(5, df.shape[0])
                            if parse_num(df.iloc[r, col]) is not None
                            and parse_num(df.iloc[r, col]) > 0)
                camps.append({'nome':name.strip(), 'inicio':inicio.strftime('%d/%m/%Y'),
                              'fim':fim.strftime('%d/%m/%Y'),
                              'tipo':str(tipo).strip() if tipo else '', 'faces':faces})
        all_camps[city] = camps

    # ── Station data ──
    def find_pct_col(df, row=5):
        if row >= df.shape[0]: return None
        for i in range(df.shape[1]):
            v = parse_num(df.iloc[row, i])
            if v is not None and 0.1 < v < 1.0:
                return i
        return None

    def read_pct(df, r, c):
        if c is None or r >= df.shape[0] or c >= df.shape[1]: return 0
        v = parse_num(df.iloc[r, c])
        return min(v, 1.0) if v is not None else 0

    def get_city_stations(ws_name, cod_col, name_col, circ_fn):
        ws = sheet.worksheet(ws_name)
        df = ws_to_df(ws)
        pc = find_pct_col(df)
        out = []
        for r in range(5, df.shape[0]):
            cod  = df.iloc[r, cod_col]
            name = df.iloc[r, name_col]
            if not (isinstance(cod,str) and cod.strip() and isinstance(name,str) and name.strip()): continue
            pct  = read_pct(df, r, pc)
            circ = circ_fn(df, r)
            out.append({'cod':cod.strip(), 'name':name.strip(), 'pct':round(pct,6), 'circuit':circ})
        return out

    stations = {
        'Rio de Janeiro': get_city_stations('Rio de Janeiro', 0, 1,
            lambda df,r: 'ORLA 1' if str(df.iloc[r,2]).strip().upper()=='SIM' else 'ORLA 2'),
        'Recife': get_city_stations('Recife', 1, 2,
            lambda df,r: 'ORLA REC' if 'orla' in str(df.iloc[r,3]).lower() else 'URBANO'),
        'Brasília':      get_city_stations('Brasilia',      0, 1, lambda df,r: 'CIRCUITO BSB'),
        'Florianópolis': get_city_stations('Florianópolis', 0, 1, lambda df,r: 'CIRCUITO FLN'),
        'Fortaleza':     get_city_stations('Fortaleza',     0, 1, lambda df,r: 'CIRCUITO FOR'),
    }

    for city in occ:
        print(f"  {city}: {occ[city]*100:.1f}% | {len(all_camps[city])} campanhas | {len(stations[city])} estações")

    return occ, weekly, circuits, all_camps, stations


# ────────────────────────────────────────────
def read_current_html():
    """Read current index.html from GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/index.html"
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github.v3+json"}
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print(f"❌ Erro ao ler HTML do GitHub: {r.status_code}")
        sys.exit(1)
    data = r.json()
    html = base64.b64decode(data['content']).decode('utf-8')
    sha  = data['sha']
    return html, sha


def update_html_content(html, occ, weekly, circuits, camps, stations):
    def esc(s): return json.dumps(s, ensure_ascii=False)
    def camp_js(lst):
        rows = [f'    {{nome:{esc(c["nome"])},inicio:{esc(c["inicio"])},fim:{esc(c["fim"])},'
                f'tipo:{esc(c["tipo"])},faces:{c.get("faces",0)}}}' for c in lst]
        return '[\n' + ',\n'.join(rows) + '\n  ]'
    def st_js(lst):
        rows = [f'    {{cod:{esc(s["cod"])},name:{esc(s["name"])},pct:{s["pct"]},'
                f'circuit:{esc(s["circuit"])}}}' for s in lst]
        return '[\n' + ',\n'.join(rows) + '\n  ]'
    def wjs(pts):
        return '[' + ','.join(f'["{p[0]}",{"null" if p[1] is None else p[1]}]' for p in pts) + ']'

    nc = 'const ACTIVE_CAMPAIGNS = {\n' + ',\n'.join(
        f'  {esc(c)}:{camp_js(camps[c])}' for c in camps) + '\n};'
    html = re.sub(r'const ACTIVE_CAMPAIGNS = \{.*?\};', lambda m: nc, html, flags=re.DOTALL)

    nd = 'const DATA = {\n' + ',\n'.join(
        f'  {esc(c)}:{{ occ:{occ[c]}, stations:{st_js(stations[c])}}}' for c in stations) + '\n};'
    html = re.sub(r'const DATA = \{.*?\};', lambda m: nd, html, flags=re.DOTALL)

    nw = 'const WEEKLY = {\n' + ',\n'.join(
        f'  {esc(c)}:{wjs(weekly[c])}' for c in weekly) + '\n};'
    html = re.sub(r'const WEEKLY = \{.*?\};', lambda m: nw, html, flags=re.DOTALL)

    ncirc = 'const CIRCUITS = {\n' + ',\n'.join(
        f'  {esc(c)}:{wjs(circuits[c])}' for c in circuits) + '\n};'
    html = re.sub(r'const CIRCUITS = \{.*?\};', lambda m: ncirc, html, flags=re.DOTALL)

    return html


def push_html(html, sha):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/index.html"
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github.v3+json",
               "Content-Type": "application/json"}
    hoje = datetime.now().strftime('%d/%m/%Y %H:%M')
    payload = {"message": f"Auto-update via Sheets — {hoje}",
               "content": base64.b64encode(html.encode('utf-8')).decode(),
               "sha": sha}
    r = requests.put(url, headers=headers, json=payload)
    if r.status_code in [200, 201]:
        print("✓ GitHub atualizado!")
        return True
    print(f"❌ Erro GitHub: {r.status_code} — {r.json().get('message','')}")
    return False


# ────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  MUDE DOOH — Auto-update (GitHub Actions)")
    print("=" * 55)

    if not GITHUB_TOKEN:
        print("❌ PAT_TOKEN não definido nos Secrets do GitHub.")
        sys.exit(1)

    print("\n🔌 Conectando ao Google Sheets...")
    sheet = connect_sheets()

    print("\n📊 Extraindo dados...")
    occ, weekly, circuits, camps, stations = extract_data(sheet)

    print("\n📥 Lendo HTML atual do GitHub...")
    html, sha = read_current_html()

    print("\n🔧 Atualizando HTML...")
    html = update_html_content(html, occ, weekly, circuits, camps, stations)

    print("\n🚀 Publicando no GitHub...")
    push_html(html, sha)

    print("\n✅ Concluído!")


if __name__ == "__main__":
    main()
