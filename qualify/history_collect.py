"""Collect public daily OHLC for the frozen price-only experiment. Uses the existing approved Yahoo host.
No extra dependencies, cookies, hosted models, current fundamentals or news. Refuses to overwrite a frozen run.
python -m qualify.history_collect --root data/history_benchmark_20261109
"""
import argparse
import json
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
from core.market import fetch_json
from .historical import ASSETS, END, digest, dump


def collect(root):
    root.mkdir(parents=True,exist_ok=True)
    if (root/'protocol.json').exists():raise ValueError('Frozen run: choose a new collection folder')
    start=int(datetime(2025,5,1,tzinfo=timezone.utc).timestamp())
    end=int(datetime.combine(END,datetime.min.time(),timezone.utc).timestamp())
    receipt={'downloaded_at':datetime.now(timezone.utc).isoformat(),'sources':[]}
    for symbol in (*ASSETS,'SPY'):
        path=root/(symbol+'.json')
        if path.exists():raise ValueError('Existing source snapshot: '+path.name)
        url=f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?period1={start}&period2={end}&interval=1d'
        raw=fetch_json(url)
        if not (raw.get('chart') or {}).get('result'):raise ValueError('Missing source result: '+symbol)
        path.write_text(json.dumps(raw),encoding='utf-8')
        receipt['sources'].append({'symbol':symbol,'url':url,'sha256':digest(path.read_bytes())})
        dump(root/'collection.json',receipt)
    return receipt


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True)
    print(json.dumps(collect(ap.parse_args().root),indent=2))
if __name__=='__main__':main()
