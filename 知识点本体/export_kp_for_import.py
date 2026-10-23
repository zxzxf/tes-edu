# -*- coding: utf-8 -*-
"""把 435 点知识点 DAG 导出为可灌 web-backend `kb_knowledge_points` 表的数据 + 扩表 SQL。

现状问题：`kb_knowledge_points` 只有 (subject, grade, title, content, tags)，
  没有 知识点编码 / 章节 / 前置关系 字段 —— 435 点 DAG 的 **前置边无处可存**，
  掌握度归因与自适应出题依赖的前置关系会丢失。
  -> 当前退化方案：把 id/chapter/section/importance/prerequisites 塞进 tags，
     保证"能灌进去、能查得到"；真正支持 DAG 需执行同目录 kb_kp_extend.sql 扩表。

用法：python export_kp_for_import.py
输出：
  知识点本体/knowledge_points_数学_435_待入库.json
  知识点本体/kb_kp_extend.sql
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DAG = os.path.join(BASE, 'knowledge_dag_高中数学_人教A版_草案.json')

EXTEND_SQL = """-- kb_knowledge_points 扩表：让 435 点 DAG 的编码与前置关系可持久化
-- 说明：均为可空列 / 新增表，属于无损变更，可安全执行。
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS code varchar(32);          -- 知识点编码 MA-B?-C?-S?-K??
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS chapter varchar(64);       -- 章
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS section varchar(64);       -- 节
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS importance varchar(16);    -- core/std
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS book varchar(32);          -- 册次
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_kp_code ON kb_knowledge_points(code);

-- 前置关系（DAG 边）单独建表，避免在知识点表里塞数组
CREATE TABLE IF NOT EXISTS kb_kp_prerequisites (
    kp_code        varchar(32) NOT NULL,
    prereq_code    varchar(32) NOT NULL,
    relation_type  varchar(16) DEFAULT 'prerequisite',
    created_at     timestamptz DEFAULT now(),
    PRIMARY KEY (kp_code, prereq_code)
);
CREATE INDEX IF NOT EXISTS idx_kb_kp_pre_prereq ON kb_kp_prerequisites(prereq_code);
"""


def main():
    d = json.load(open(DAG, encoding='utf-8'))
    nodes = d['nodes']

    rows = []
    for n in nodes:
        rows.append({
            'subject': '数学',
            'grade': '高中',
            'title': n['name'],
            'content': f"{n['book']} / {n['chapter']} / {n['section']}",
            'tags': [
                n['id'],                       # 知识点编码（检索/归因主键）
                n['chapter'], n['section'],     # 章节归属
                n.get('importance', 'std'),     # 高频标记
                f"前置{len(n.get('prerequisites') or [])}",
            ],
            # 扩表后启用（当前表无这些列，灌库时忽略）
            'code': n['id'],
            'chapter': n['chapter'],
            'section': n['section'],
            'importance': n.get('importance', 'std'),
            'book': n['book'],
        })

    out = os.path.join(BASE, 'knowledge_points_数学_435_待入库.json')
    json.dump(rows, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    edges = [(n['id'], p) for n in nodes for p in (n.get('prerequisites') or [])]
    edges_path = os.path.join(BASE, 'kb_kp_prerequisites_待入库.json')
    json.dump([{'kp_code': a, 'prereq_code': b} for a, b in edges],
              open(edges_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    sql_path = os.path.join(BASE, 'kb_kp_extend.sql')
    open(sql_path, 'w', encoding='utf-8').write(EXTEND_SQL)

    print(f'知识点 {len(rows)} 条 -> {out}')
    print(f'前置边 {len(edges)} 条 -> {edges_path}')
    print(f'扩表 SQL -> {sql_path}')
    # 自检：title 长度（表限制 String(255)）、tags 是否含会被误拆的字符
    over = [r['title'] for r in rows if len(r['title']) > 255]
    print('title 超 255 字符:', len(over))


if __name__ == '__main__':
    main()
