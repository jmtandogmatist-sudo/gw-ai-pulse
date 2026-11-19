import sys, json, re
sys.path.insert(0, '.')
from collector.config import load_config, get_secret
import requests
cfg = load_config(); s = cfg.settings['discourse']
base = s['base_url'].rstrip('/')
H = {'Api-Username': s['api_username'], 'Api-Key': get_secret(s['api_key_secret'])}
r = requests.get(base + '/raw/632/1', headers=H, timeout=25, verify=s.get('verify_tls', True))
F = chr(96) * 3
m = re.search(F + r'json\s*(\{.*?\})\s*' + F, r.text, re.S) or re.search(r'(\{.*\})', r.text, re.S)
d = json.loads(m.group(1))
print('issue_date     :', d.get('issue_date'), '| radar_updated_at:', d.get('radar_updated_at'))
print('radar 条数     :', len(d.get('radar') or []), '| 正文', len(r.text), '字符')
empty = sum(1 for i in d['radar'] if not (i.get('topic_url') or ''))
print('radar topic_url 为空:', empty, '(权限已开放，应为 0)')
print('')
print('01 今天发生了什么：')
for b in d.get('brief') or []:
    print('  -', b.get('source'), '|', b.get('text', '')[:44])
print('')
lead = d.get('lead') or {}
print('02 焦点：', lead.get('cred'), '|', lead.get('title', '')[:44])
print('   类型：', lead.get('kind', '（旧格式，无 kind）'), '| 摘要', len(lead.get('summary') or ''), '字 | 链接', lead.get('url', '')[:56])
print('   来源：', [x['label'] for x in (lead.get('sources') or [])])
print('')
print('02 右栏：')
for e in d.get('energy') or []:
    print('  -', e.get('from'), '|', e.get('title', '')[:36])
print('')
print('04 今日值得看：')
for p in d.get('picks') or []:
    print('  -', p.get('url', '')[:62])
srcs = [b.get('source') for b in (d.get('brief') or []) if b.get('source')]
# 一手来源白名单就是 keep_bypass.sources（settings.yaml），这里直接复用，
# 免得校验脚本的看法和采集器的取舍标准各说一套（原先只认三家模型厂商，
# 新华网能源频道这类 authority 明明是一手，却判成「官方源没上 01」）。
first_hand = set((cfg.settings.get('keep_bypass') or {}).get('sources') or [])
hits = [x for x in srcs if x in first_hand]
# 2026-11-19 起一手源可能整条变成 02 焦点长文、从 01 让位出去，这种情况不算「一手源缺席」
lead_first_hand = [x['label'] for x in (lead.get('sources') or []) if x['label'] in first_hand]
hits = hits or lead_first_hand
print('')
print('判定：01 信源去重 ->', len(set(srcs)) == len(srcs),
      '| 一手源上 01 ->', bool(hits), hits)
# 2026-11-19 起焦点单独成篇：02 = 焦点长文入口卡，且这条不能再出现在 01 里
if lead.get('kind') == 'focus':
    dup = any(lead.get('title') == b.get('text') for b in (d.get('brief') or []))
    print('判定：焦点卡 ->', 'kind=focus、链接到话题', bool(lead.get('url')),
          '| 01 让位（不重复） ->', not dup,
          '| 摘要 ≤320 字 ->', len(lead.get('summary') or '') <= 320)