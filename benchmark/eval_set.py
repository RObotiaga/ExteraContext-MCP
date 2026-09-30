import argparse,json,sys,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts')); import query
ap=argparse.ArgumentParser(); ap.add_argument('cases'); ap.add_argument('--out'); a=ap.parse_args()
cases=json.loads(Path(a.cases).read_text('utf-8')); rows=[]
for c in cases:
 fs=query.search_facts(c['query'],10); ids=[x['id'] for x in fs]
 if 'expected' in c:
  exp=set(c['expected']); ranks={e:(ids.index(e)+1 if e in ids else None) for e in exp}; vals=[v for v in ranks.values() if v]
  rows.append({'id':c['id'],'ranks':ranks,'top1':bool(vals and min(vals)<=1),'top3':bool(vals and min(vals)<=3),'top5':bool(vals and min(vals)<=5),'top10':bool(vals),'recall':sum(e in ids for e in exp)/len(exp),'top_ids':ids[:5]})
 elif 'expected_prefix' in c:
  prefix=c['expected_prefix']; sym=c.get('symbol')
  pos=next((i+1 for i,fid in enumerate(ids) if fid.startswith(prefix) or (sym and fid==sym)),None)
  rows.append({'id':c['id'],'ranks':pos,'top1':bool(pos and pos<=1),'top3':bool(pos and pos<=3),'top5':bool(pos and pos<=5),'top10':bool(pos and pos<=10),'recall':1.0 if pos else 0.0,'top_ids':ids[:5]})
s={'cases':len(rows)}
for k in ['top1','top3','top5','top10','recall']: s[k]=statistics.mean(r[k] for r in rows)
o={'summary':s,'cases':rows}; txt=json.dumps(o,ensure_ascii=False,indent=2); print(txt)
if a.out: Path(a.out).write_text(txt,'utf-8')
