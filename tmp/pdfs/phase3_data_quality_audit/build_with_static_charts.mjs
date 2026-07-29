import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { buildPortableArtifact } from "file:///C:/Users/patel/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.8-13ceeea1f599/skills/build-report/scripts/build_portable_artifact.mjs";
import { extractPortableChartSvgs } from "file:///C:/Users/patel/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.8-13ceeea1f599/skills/build-report/scripts/extract_portable_chart_svgs.mjs";

const inputPath = resolve(process.argv[2]);
const outputPath = resolve(process.argv[3]);
if (!inputPath || !outputPath) throw new Error("input and output paths required");

const artifact = JSON.parse(readFileSync(inputPath, "utf8"));
writeFileSync(outputPath, buildPortableArtifact(artifact), "utf8");

const staticCharts = await extractPortableChartSvgs({
  actionTimeoutMs: 5000,
  htmlPath: outputPath,
  readyTimeoutMs: 15000
});

writeFileSync(outputPath, buildPortableArtifact(artifact, { staticCharts }), "utf8");
process.stdout.write(`${JSON.stringify({ ok: true, charts: Object.keys(staticCharts).length, outputPath })}\n`);
