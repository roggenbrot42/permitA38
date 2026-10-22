/* permitA38 – start-up (kept out of index.html so a strict Content-Security-Policy works). */
(async () => { try { await bootstrap(); } catch (e) { S.me = null; } render(); })();
