from __future__ import annotations
import csv, json, re, sys
from pathlib import Path
import requests
from bs4 import BeautifulSoup

LIST_URL='https://letterboxd.com/danyel/list/the-other-20th-century/'
ATLAS=Path('atlas/top_10_by_country_letterboxd_import.csv')
OUT=Path('output/list_overlap.json')


def normalize(uri: str) -> str:
    m=re.search(r'https?://letterboxd\.com/film/([^/]+)/?', uri)
    return m.group(1) if m else uri.strip().rstrip('/').split('/')[-1]

with ATLAS.open(encoding='utf-8-sig', newline='') as f:
    atlas_rows=list(csv.DictReader(f))
atlas={normalize(r['LetterboxdURI']):r for r in atlas_rows}

session=requests.Session(); session.headers['User-Agent']='Mozilla/5.0'
other={}
page=1
while True:
    url=LIST_URL if page==1 else f'{LIST_URL}page/{page}/'
    r=session.get(url, timeout=45); r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    for node in soup.select('[data-film-slug], [data-target-link], a[href*="/film/"]'):
        slug=node.get('data-film-slug')
        href=node.get('data-target-link') or node.get('href') or ''
        if not slug:
            m=re.search(r'/film/([^/]+)/', href)
            slug=m.group(1) if m else None
        if not slug or slug in other:
            continue
        title=node.get('data-film-name') or node.get('alt') or node.get('title') or slug
        other[slug]={'slug':slug,'title':title}
    nxt=soup.select_one('a.next') or soup.find('a', string=re.compile('Next', re.I))
    if not nxt or page>=20:
        break
    page+=1

intersection=sorted(set(atlas)&set(other))
result={
 'atlas_count':len(atlas),
 'other_20th_century_count':len(other),
 'overlap_count':len(intersection),
 'distinct_union_count':len(set(atlas)|set(other)),
 'only_atlas_count':len(set(atlas)-set(other)),
 'only_other_count':len(set(other)-set(atlas)),
 'overlap':[{'slug':s,'atlas_title':atlas[s]['Title'],'other_title':other[s]['title']} for s in intersection],
}
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='overlap'},ensure_ascii=False,indent=2))
if len(other)!=484:
    print(f'Expected 484 films, got {len(other)}', file=sys.stderr)
    sys.exit(2)
