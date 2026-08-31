-- Retire obsolete API readers after the v2 run-bound contract and a published
-- user_scope snapshot have both been verified.
DROP TABLE FUNCTION IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_events`;
DROP TABLE FUNCTION IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_user_list`;
DROP VIEW IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_overview`;
DROP VIEW IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_user_detail`;
DROP TABLE IF EXISTS `${PROJECT_ID}.${DATASET_ID}.user_daily`;
