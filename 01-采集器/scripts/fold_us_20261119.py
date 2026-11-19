
import sys, json, sqlite3, re
sys.path.insert(0, '.')
from collector.config import load_config
from collector.discourse import Discourse

US_OPEN = '[details="AI 研判（自动生成，非公司结论）"]'
PAT = re.compile(r'\[wrap=ais-us\]\s*\n\s*\n\*\*对金风\*\*\s*\n\s*\n(.*?)\n\s*\n\[/wrap\]', re.S)

cfg = load_config()
forum = Discourse(cfg)
db = sqlite3.connect('state/index.sqlite')
rows = db.execute("select json_extract(data,'$.topic_id'), json_extract(data,'$.post_id'), json_extract(data,'$.title') from signals where json_extract(data,'$.post_id') is not null order by rowid").fetchall()
print('待处理', len(rows), flush=True)
ok = skip = fail = 0
for topic_id, post_id, title in rows:
    try:
        raw = forum.post_raw(post_id)
    except Exception as e:
        print('READ FAIL', post_id, e, flush=True); fail += 1; continue
    if US_OPEN in raw:
        skip += 1; continue
    new, n = PAT.subn(lambda m: US_OPEN + '\n\n' + m.group(1).strip() + '\n\n[/details]', raw)
    if n == 0:
        skip += 1; continue
    try:
        forum.update_post(post_id, new, '对金风研判改为折叠块（2026-11-19）')
        ok += 1
        if ok % 10 == 0: print('  ...已改', ok, flush=True)
    except Exception as e:
        print('WRITE FAIL', post_id, e, flush=True); fail += 1
print(f'DONE ok={ok} skip={skip} fail={fail} requests={getattr(forum,"requests_made",0)}', flush=True)
