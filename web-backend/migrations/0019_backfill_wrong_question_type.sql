-- 0019_backfill_wrong_question_type.sql
-- 一次性回填历史错题的 question_type，让错题本「题型」筛选立刻全覆盖。
--
-- 背景：0018 只按 paper_question_id 回填了 exam 来源的错题；更早的行既没有 paper_question_id，
--       也有 App 手工录入的，需要另找依据。这里分四档，逐档收紧，宁可留空也不猜。
--       注意：**不能用 wrong_questions.content_hash 关联 course_kb** —— 生产库里存在错配
--       （如 id=89「1+1等于多少」的 hash 指向「若 z/(z-1)=1+i，则 z=（ ）」），会填错题型。
--
-- 幂等：每档都带 question_type IS NULL 守卫，可重复执行。
-- 回滚：备份表 wrong_questions_qtype_bak_20261105 保留回填前的原值。
--
-- 2026-11-05 在生产库（tes_edu @ 106.12.25.5）执行，结果：22 条 active 全量补全。

BEGIN;

-- 回填前快照（只建一次）
CREATE TABLE IF NOT EXISTS wrong_questions_qtype_bak_20261105 AS
SELECT id, question_type, now() AS backed_up_at
FROM wrong_questions;

-- 档 A：exam 来源，按题干精确匹配卷题快照。
--   只认「同题干的所有快照题型一致」的，避免同一题出现在多份卷子里时产生歧义。
UPDATE wrong_questions w
SET question_type = s.qtype
FROM (
    SELECT btrim(stem) AS stem_key, min(qtype) AS qtype
    FROM exam_paper_questions
    WHERE qtype IN ('choice', 'blank', 'subjective')
    GROUP BY btrim(stem)
    HAVING count(DISTINCT qtype) = 1
) s
WHERE w.question_type IS NULL
  AND w.deleted_at IS NULL
  AND btrim(w.question) = s.stem_key;

-- 档 B：非 exam 来源，与题库 course_kb 题干高度相似且唯一最优，题型取题库的 type。
--   score 为 difflib 归一化相似度（≥0.70），逐行人工核对过题干对应关系；kb_id 仅作追溯用。
--   单选/多选 → choice，填空 → blank，解答 → subjective。
UPDATE wrong_questions w
SET question_type = m.qtype
FROM (VALUES
    (90,  'choice', 114, 0.780),  -- 已知函数 f(x)=x+2e^x+a 的最大值为 1，则 a=（ ）
    (91,  'choice', 114, 0.733),  -- 同上（OCR 版：x+2/e×+a）
    (92,  'choice', 114, 0.776),  -- 同上（手打版）
    (95,  'choice', 114, 0.780),  -- 同上（LaTeX 版）
    (96,  'choice', 114, 0.733),  -- 同上（OCR 版）
    (99,  'blank',  101, 0.867),  -- 若直线 y=2x+5 是曲线 y=e^x+x+a 的切线，则 a=___
    (101, 'choice',  98, 0.800)   -- 正三棱柱 ABC-A1B1C1 中 D 为 BC 中点（题库为多选）
) AS m(wid, qtype, kb_id, score)
WHERE w.id = m.wid
  AND w.question_type IS NULL
  AND w.deleted_at IS NULL;

-- 档 C：题干自带填空线 → 填空题（_ 或全角 ＿ 连续 3 个以上）。
UPDATE wrong_questions w
SET question_type = 'blank'
WHERE w.question_type IS NULL
  AND w.deleted_at IS NULL
  AND (w.question ~ '_{3,}' OR w.question ~ '＿{2,}');

-- 档 D：无选项、无填空线、题库里也没有对应题 → 按主观题兜底。
--   仅 4 行，且都是演示/测试数据（「1+1等于多少」「1+5等于多少」等）。
--   若要撤回这几行，用备份表按 id 还原即可。
UPDATE wrong_questions w
SET question_type = 'subjective'
WHERE w.question_type IS NULL
  AND w.deleted_at IS NULL
  AND w.id IN (89, 97, 98, 112);

-- 档 E：让已经同步过的客户端能重新拉到这些行。
--   错题增量拉取按 `updated_at >= since` 过滤（`wrongbook.list_sync`），只改题型不动 updated_at，
--   手机端会一直停在旧值（App 传的是上次同步的服务端时间戳，不是 0）。
--   把本次补过型的行提一档；重复执行无害（只是让客户端多拉一次）。
UPDATE wrong_questions w
SET updated_at = now()
WHERE w.question_type IS NOT NULL
  AND w.deleted_at IS NULL
  AND w.id IN (
      SELECT b.id FROM wrong_questions_qtype_bak_20261105 b
      WHERE b.question_type IS NULL
  );

COMMIT;
