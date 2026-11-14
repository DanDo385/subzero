SELECT 'provider stats' AS section;
SELECT * FROM provider_stats;

SELECT 'scoreboard' AS section;
SELECT * FROM scoreboard;

SELECT 'races' AS section;
SELECT id, started_at, winner, pattern, side, price_ticks, qty
FROM races
ORDER BY id;

SELECT 'calls' AS section;
SELECT id, race_id, provider, role, method, ok,
       round(duration_ms, 1) AS ms,
       http_status, result_preview, error
FROM api_calls
ORDER BY id;
