-- Источник таблиц — исходная Excel-книга; имена и значения не перенормализуются.
-- Строгий срез повторяет scripts/workbook.py: это текущие заведения, НЕ давность отзывов.
CREATE TEMP VIEW verified_concrete AS
SELECT m.*, r.event_group, r.platform_verification, r.bias_flag,
       COALESCE(NULLIF(r.event_group,''), r.review_id) AS event_key,
       CASE WHEN m.confidence='high' AND v.sample_current='yes'
                  AND COALESCE(r.platform_verification,'') <> 'not_passed'
                  AND COALESCE(r.bias_flag,'') = '' THEN 1 ELSE 0 END AS strict_flag
FROM mentions m
JOIN reviews r ON r.review_id=m.review_id AND r.venue_id=m.venue_id
JOIN venues v ON v.venue_id=m.venue_id
WHERE m.source_status='verified' AND r.source_status='verified'
  AND COALESCE(r.duplicate_of,'')='' AND m.specificity='concrete'
  AND COALESCE(m.normalized_flavor,'') <> '';

-- Один вкус / одно событие = максимум один голос. Разные тона становятся mixed.
CREATE TEMP VIEW event_opinions AS
SELECT normalized_flavor,event_key,
       CASE WHEN COUNT(DISTINCT sentiment)>1 THEN 'mixed' ELSE MIN(sentiment) END AS sentiment,
       MAX(CASE WHEN evidence_type LIKE '%дегустац%' THEN 1 ELSE 0 END) AS tasting,
       MAX(strict_flag) AS any_strict
FROM verified_concrete GROUP BY normalized_flavor,event_key;

CREATE TEMP VIEW strict_event_opinions AS
SELECT normalized_flavor,event_key,
       CASE WHEN COUNT(DISTINCT sentiment)>1 THEN 'mixed' ELSE MIN(sentiment) END AS sentiment
FROM verified_concrete WHERE strict_flag=1 GROUP BY normalized_flavor,event_key;

CREATE TEMP VIEW candidate_stats AS
SELECT c.*,
 (SELECT COUNT(*) FROM verified_concrete m WHERE m.normalized_flavor=c.normalized_flavor) AS verified_mentions,
 (SELECT COUNT(DISTINCT review_id) FROM verified_concrete m WHERE m.normalized_flavor=c.normalized_flavor) AS unique_reviews,
 (SELECT COUNT(DISTINCT venue_id) FROM verified_concrete m WHERE m.normalized_flavor=c.normalized_flavor) AS unique_venues,
 (SELECT COUNT(*) FROM event_opinions e WHERE e.normalized_flavor=c.normalized_flavor) AS unique_events,
 (SELECT COUNT(*) FROM event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND sentiment='positive') AS positive,
 (SELECT COUNT(*) FROM event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND sentiment='negative') AS negative,
 (SELECT COUNT(*) FROM event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND sentiment='mixed') AS mixed,
 (SELECT COUNT(*) FROM event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND sentiment='neutral') AS neutral,
 (SELECT COUNT(*) FROM event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND sentiment='mention_only') AS mention_only,
 (SELECT COUNT(*) FROM event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND tasting=1) AS tasting_events,
 (SELECT COUNT(*) FROM event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND tasting=1 AND sentiment='positive') AS tasting_positive,
 (SELECT COUNT(*) FROM verified_concrete m WHERE m.normalized_flavor=c.normalized_flavor AND strict_flag=1) AS strict_mentions,
 (SELECT COUNT(*) FROM verified_concrete m WHERE m.normalized_flavor=c.normalized_flavor AND strict_flag=1 AND sentiment='positive') AS strict_positive,
 (SELECT COUNT(*) FROM verified_concrete m WHERE m.normalized_flavor=c.normalized_flavor AND strict_flag=1 AND sentiment='negative') AS strict_negative,
 (SELECT COUNT(*) FROM verified_concrete m WHERE m.normalized_flavor=c.normalized_flavor AND strict_flag=1 AND sentiment='mixed') AS strict_mixed,
 (SELECT COUNT(*) FROM strict_event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND sentiment='positive') AS strict_event_positive,
 (SELECT COUNT(*) FROM strict_event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND sentiment='negative') AS strict_event_negative,
 (SELECT COUNT(*) FROM strict_event_opinions e WHERE e.normalized_flavor=c.normalized_flavor AND sentiment='mixed') AS strict_event_mixed,
 (SELECT COUNT(DISTINCT venue_id) FROM verified_concrete m WHERE m.normalized_flavor=c.normalized_flavor AND strict_flag=1) AS strict_venues,
 (SELECT COUNT(*) FROM menu n JOIN venues v ON v.venue_id=n.venue_id
   WHERE n.normalized_flavor=c.normalized_flavor AND n.source_status='verified'
     AND v.sample_current='yes') AS verified_current_menu_rows
FROM candidate_roles c;
