-- Destructive cleanup is intentionally separate from compatible publication of
-- the canonical dashboard_events/dashboard_user_list table functions.
-- Run only after the old Monitor revision has zero traffic and the reviewed
-- dependency receipt proves zero readers of these legacy objects.
DROP VIEW IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_overview`;
DROP VIEW IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_user_detail`;
DROP TABLE IF EXISTS `${PROJECT_ID}.${DATASET_ID}.user_daily`;
