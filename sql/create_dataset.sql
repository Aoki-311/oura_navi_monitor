-- Render ${PROJECT_ID}, ${DATASET_ID}, ${BQ_LOCATION} before execution.
CREATE SCHEMA IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}`
OPTIONS (
  location = '${BQ_LOCATION}',
  description = 'OurA Navi Monitor single analytics contract'
);
