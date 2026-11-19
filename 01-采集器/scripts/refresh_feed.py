"""只刷底座帖（632）：不发帖、不改分类，用来把 render 侧的新规则立即套到今天的数据上。

步骤：读 632 → 打印旧 04 → update_feed（community_posts 传 None，社区两块原样保留）
→ 再读一次打印新 04 / 01。
"""
import sys
from datetime import datetime
sys.path.insert(0, '.')
from collector.config import load_config
from collector.discourse import Discourse
from collector.pipeline import update_feed
from collector.render import read_feed
from collector.sources.fetchers import CST
from collector.store import Store

cfg = load_config()
forum = Discourse(cfg)
store = Store(cfg.root / 'state' / 'index.sqlite')
post_id = forum.first_post_id(cfg.settings['feed']['topic_id'])
before, _ = read_feed(forum.post_raw(post_id))
print('旧 04:', [p['url'][:58] for p in before.get('picks') or []])
print('旧 01:', [b['source'] for b in before.get('brief') or []])

n = update_feed(cfg, forum, store, datetime.now(CST))
print('update_feed ->', n)

after, _ = read_feed(forum.post_raw(post_id))
print('新 04:', [p['url'][:58] for p in after.get('picks') or []])
print('新 01:', [b['source'] for b in after.get('brief') or []])
print('雷达', len(after.get('radar') or []), '条 | community', len(after.get('community') or []), '条 | question:', (after.get('question') or '')[:30])
