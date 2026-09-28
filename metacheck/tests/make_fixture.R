# build a fixture run dir without mc_import/mc_run:  Rscript tests/make_fixture.R <run_dir>
skill <- dirname(dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))))
source(file.path(skill, "scripts", "_common.R"))
run_dir <- commandArgs(TRUE)[1]
mc_load()
p <- demopaper()
dir.create(file.path(run_dir, "modules"), recursive = TRUE, showWarnings = FALSE)
saveRDS(p, file.path(run_dir, "paper.rds"))
m <- module_run(p, "marginal")
tab <- as.data.frame(m$table)
mc_write_json(list(
  module = "marginal", title = m$title, section = m$section, args = list(), status = "ok", error = NULL,
  upstream = list(), elapsed_s = 0.1, traffic_light = m$traffic_light, summary_text = m$summary_text,
  report = as.list(m$report), summary_table = m$summary_table,
  table = cbind(mc_candidate_ids(tab, p$paper_id, "marginal")[, c("candidate_id", "item_id")], tab)
), file.path(run_dir, "modules", "marginal.json"))
# a module whose API call failed: must never look clean
mc_write_json(list(
  module = "ref_retraction", title = "Retraction Watch", section = "reference", args = list(), status = "failed",
  error = "HTTP 503 from upstream", upstream = list(), elapsed_s = 2, traffic_light = NULL, summary_text = NULL,
  report = list(), summary_table = list(), table = list()
), file.path(run_dir, "modules", "ref_retraction.json"))
mc_manifest_update(run_dir, list(
  run_id = "fixture", paper_id = p$paper_id, source = list(path = "demo", sha256 = "0000"),
  metacheck = list(version = as.character(utils::packageVersion("metacheck")), commit = "fixture"),
  r_version = R.version.string, databases = list(retractionwatch = "2025-01-01"),
  schema_versions = list(manifest = "1", module_result = "1", finding = "1"),
  model_id = "fixture-model", created_at = mc_now()))
