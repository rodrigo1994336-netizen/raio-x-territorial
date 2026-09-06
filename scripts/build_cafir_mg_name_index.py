from __future__ import annotations

import gzip
import json
import re
import shutil
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DAV='https://arquivos.receitafederal.gov.br/public.php/dav/files/RRmcpB2tf5cXskz'
SNAPSHOT='D60901'
PARTS=('MG01','MG02','MG03')
GENERIC=('FAZENDA','SITIO','SÍTIO','CHACARA','CHÁCARA','ESTANCIA','ESTÂNCIA','RANCHO','GLEBA','PROPRIEDADE','IMOVEL RURAL','IMÓVEL RURAL','AREA RURAL','ÁREA RURAL')


def norm(v):
    s=''.join(c for c in unicodedata.normalize('NFKD',str(v or '')) if not unicodedata.combining(c))
    s=re.sub(r'[^A-Z0-9 ]+',' ',s.upper())
    return ' '.join(s.split())


def alias(v):
    s=norm(v)
    changed=True
    while changed:
        changed=False
        for g in GENERIC:
            n=norm(g)
            if s==n:return s
            if s.startswith(n+' '):
                s=s[len(n):].strip(' -–—')
                changed=True
                break
    return s


def dec(b):
    for enc in ('utf-8','cp1252','latin1'):
        try:return b.decode(enc).strip()
        except UnicodeDecodeError:pass
    return b.decode('latin1','replace').strip()


def get(url,retries=7):
    last=None
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Raio-X-Territorial/CAFIR-NameIndex-V1','Accept':'text/csv,*/*'})
            with urllib.request.urlopen(req,timeout=180) as r:return r.read()
        except Exception as exc:
            last=exc
            if attempt+1<retries:
                pause=min(18,2.0*(attempt+1))
                print(f'DOWNLOAD_RETRY attempt={attempt+1}/{retries} wait={pause}s error={type(exc).__name__}:{str(exc)[:180]}',flush=True)
                time.sleep(pause)
    raise last


def shard_for(a):
    c=(a[:1] or '_').upper()
    return c if c.isalnum() else '_'


def main():
    root=Path(__file__).resolve().parents[1]
    out=root/'data/cafir/name_shards/MG'
    if out.exists():shutil.rmtree(out)
    out.mkdir(parents=True,exist_ok=True)
    shards=defaultdict(list);stats=Counter();files=[]
    for part in PARTS:
        raw=get(f'{DAV}/K34313UF.{SNAPSHOT}.{part}.csv')
        total=active=named=bad=0
        for line in raw.splitlines():
            total+=1;r=line.rstrip(b'\r\n')
            if len(r)!=245:bad+=1;continue
            if r[85:87]!=b'02':continue
            active+=1
            name=dec(r[30:85])
            if not name:continue
            named+=1
            a=alias(name)
            if len(a)<2:continue
            area_raw=r[8:17].decode('ascii','ignore')
            incra=r[17:30].decode('ascii','ignore').strip()
            nirf=r[0:8].decode('ascii','ignore').strip()
            mun=norm(dec(r[185:225]))
            rec=[a,name,mun,int(area_raw) if area_raw.isdigit() else None,incra,nirf]
            shards[shard_for(a)].append(rec)
        files.append({'part':part,'total':total,'active':active,'named':named,'bad':bad})
        stats.update(total=total,active=active,named=named,bad=bad)
        print(part,total,active,named,bad,flush=True)
    manifest={}
    indexed_records=0
    for key,rows in sorted(shards.items()):
        rows.sort(key=lambda x:(x[0],x[2],x[3] if x[3] is not None else -1,x[4],x[5]))
        p=out/f'{key}.jsonl.gz'
        with gzip.open(p,'wt',encoding='utf-8',compresslevel=9) as f:
            for r in rows:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
        indexed_records+=len(rows)
        manifest[key]={'records':len(rows),'bytes':p.stat().st_size,'file':p.name}
        print('SHARD',key,len(rows),p.stat().st_size,flush=True)
    meta={
      'source':'Receita Federal / CAFIR - compartilhamento público oficial',
      'dav_root':DAV,'snapshot':SNAPSHOT,'uf':'MG','generated_at_utc':datetime.now(timezone.utc).isoformat(),
      'record_contract':'[normalized_alias,display_name,municipality_normalized,area_tenths_ha,incra_code,nirf]',
      'active_records':stats['active'],'active_named_records':stats['named'],'indexed_named_records':indexed_records,'files':files,'shards':manifest,
      'coverage_statement':'All active MG CAFIR records with a usable denomination and searchable normalized alias (>=2 chars) in snapshot D60901 are represented in exactly one alias shard. This is CAFIR-record coverage, not CAR->name coverage.',
      'personal_data_fields_in_index':False,
    }
    (out/'meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(meta,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
