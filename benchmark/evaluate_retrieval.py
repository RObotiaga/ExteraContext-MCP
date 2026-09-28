from __future__ import annotations
import json, re, sys, statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import query as qmod

cases=json.loads((Path(__file__).with_name('cases.json')).read_text('utf-8'))

def rr(ids, expected):
    for i,x in enumerate(ids,1):
        if x in expected: return 1/i
    return 0.0

rows=[]
for case in cases:
    facts=qmod.search_facts(case['query'],limit=10)
    ids=[f['id'] for f in facts]
    exp=set(case['expected'])
    ranks={e:(ids.index(e)+1 if e in ids else None) for e in exp}
    any_rank=min([r for r in ranks.values() if r is not None], default=None)
    found=sum(1 for e in exp if e in ids)
    direct=sum(1 for f in facts if f.get('directness') in {'official','target-ecosystem'})
    donors=sum(1 for f in facts if f.get('directness')=='donor')
    packet=qmod.context_packet(case['query'],'ExteraGram Android','12.10.1','1.4.5.5',10)
    packet_md=qmod.render_context_md(packet)
    rows.append({
        'id':case['id'],'top1': bool(any_rank and any_rank<=1),'top3':bool(any_rank and any_rank<=3),
        'top5':bool(any_rank and any_rank<=5),'top10':bool(any_rank),'expected_recall':found/len(exp),
        'mrr':rr(ids,exp),'direct':direct,'donors':donors,'chars':len(packet_md),'ranks':ranks,
        'top_ids':ids[:5]
    })

summary={
 'cases':len(rows),
 'hit@1':sum(r['top1'] for r in rows)/len(rows),
 'hit@3':sum(r['top3'] for r in rows)/len(rows),
 'hit@5':sum(r['top5'] for r in rows)/len(rows),
 'hit@10':sum(r['top10'] for r in rows)/len(rows),
 'expected_recall@10':statistics.mean(r['expected_recall'] for r in rows),
 'MRR@10':statistics.mean(r['mrr'] for r in rows),
 'avg_direct_top10':statistics.mean(r['direct'] for r in rows),
 'avg_donor_top10':statistics.mean(r['donors'] for r in rows),
 'avg_packet_chars':statistics.mean(r['chars'] for r in rows),
}
print(json.dumps({'summary':summary,'cases':rows},ensure_ascii=False,indent=2))
