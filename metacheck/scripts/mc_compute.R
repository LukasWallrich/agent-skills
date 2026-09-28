#!/usr/bin/env Rscript
# Calculator for the agent: every number in a report that is not quoted from the paper
# must come from here. Usage:
#   Rscript mc_compute.R --op <name> [flags]     |  --json '{"op":"...", ...}'  |  --op list
# Output: {status, op, inputs, formula, engine, result}

source(file.path(dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])), "_common.R"))

OPS <- list(
  p_from_stat = "--stat t|F|r|chi2|z --value <x> [--df <df> | --df1 <a> --df2 <b> | --n <n> (r only)] [--tails 1|2 (default 2)]",
  statcheck = "--text \"t(28) = 2.20, p = .03\" [--one-tailed]",
  implied_es = "--stat t --value <t> --design within|between [--df <df>] [--n <n> (within)] [--n1 <a> --n2 <b> (between)] [--reported <d>] [--tol 0.01]  |  --stat F --value <F> --df1 <a> --df2 <b> [--reported <eta2p>]",
  power = "--test t_one|t_paired|t_two|r|anova --es <d|r|f> [--n <per group (pairs for t_paired, total for r)> | --power <0-1>] [--k <groups> (anova)] [--alpha 0.05] [--tails 1|2] [--reported-n <n>]",
  convert = "--from d|r --value <x>   (d<->r, equal-n assumption)",
  list = "print this list"
)

num <- function(a, key, required = TRUE) {
  v <- a[[key]]
  if (is.null(v)) {
    if (required) stop("Missing required argument: --", gsub("_", "-", key))
    return(NULL)
  }
  x <- suppressWarnings(as.numeric(v))
  if (is.na(x)) stop("--", gsub("_", "-", key), " must be numeric, got: ", v)
  x
}

# ±tol comparison of a reported value against computed candidates (abs values, as in stat_effect_size)
compare <- function(reported, candidates, tol) {
  if (is.null(reported)) return(NULL)
  diffs <- lapply(candidates, \(x) abs(abs(reported) - abs(x)))
  list(reported = reported, tol = tol, abs_diff = diffs,
       match = lapply(diffs, \(x) x <= tol), any_match = any(unlist(diffs) <= tol))
}

op_p_from_stat <- function(a) {
  stat <- a$stat %||% stop("Missing required argument: --stat")
  x <- num(a, "value")
  tails <- num(a, "tails", FALSE) %||% 2
  if (!tails %in% 1:2) stop("--tails must be 1 or 2")
  if (stat == "t") {
    df <- num(a, "df")
    p <- tails * stats::pt(abs(x), df, lower.tail = FALSE)
    f <- "p = tails * pt(|t|, df, lower.tail = FALSE)"
  } else if (stat == "z") {
    p <- tails * stats::pnorm(abs(x), lower.tail = FALSE)
    f <- "p = tails * pnorm(|z|, lower.tail = FALSE)"
  } else if (stat == "r") {
    df <- num(a, "df", FALSE) %||% (num(a, "n") - 2)
    if (abs(x) >= 1) stop("|r| must be < 1")
    tval <- x * sqrt(df / (1 - x^2))
    p <- tails * stats::pt(abs(tval), df, lower.tail = FALSE)
    f <- "t = r * sqrt(df / (1 - r^2)), df = n - 2; p = tails * pt(|t|, df, lower.tail = FALSE)"
  } else if (stat == "F") {
    p <- stats::pf(x, num(a, "df1"), num(a, "df2"), lower.tail = FALSE)
    if (tails == 1) p <- p / 2
    f <- "p = pf(F, df1, df2, lower.tail = FALSE); halved if --tails 1 (only meaningful for df1 = 1)"
  } else if (stat == "chi2") {
    p <- stats::pchisq(x, num(a, "df"), lower.tail = FALSE)
    if (tails == 1) p <- p / 2
    f <- "p = pchisq(chi2, df, lower.tail = FALSE); halved if --tails 1 (only meaningful for df = 1)"
  } else {
    stop("--stat must be one of t, F, r, chi2, z")
  }
  list(formula = f, engine = "base R stats",
       result = list(p = p, p_rounded3 = round(p, 3), tails = tails,
                     note = if (tails == 1) "one-tailed p assumes the effect is in the predicted direction" else NULL))
}

op_statcheck <- function(a) {
  text <- a$text %||% stop("Missing required argument: --text")
  one <- isTRUE(a$one_tailed) || identical(a$one_tailed, "true")
  if (!requireNamespace("statcheck", quietly = TRUE)) stop("statcheck is not installed")
  utils::capture.output(sc <- statcheck::statcheck(text, messages = FALSE, OneTailedTests = one))
  rows <- if (is.null(sc)) data.frame() else as.data.frame(sc)
  list(formula = "statcheck::statcheck(text, messages = FALSE, OneTailedTests = one_tailed)",
       engine = paste("statcheck", utils::packageVersion("statcheck")),
       result = list(n = nrow(rows), rows = rows,
                     note = if (!nrow(rows)) "statcheck extracted no APA-formatted test from the text; this is not a pass" else NULL))
}

# Formulas reimplemented from metacheck inst/modules/stat_effect_size.R
# (classify_d_coherence / classify_f_coherence): they are closures inside the module
# function and cannot be called. Same arithmetic, same default tolerance (0.01).
op_implied_es <- function(a) {
  stat <- a$stat %||% stop("Missing required argument: --stat")
  x <- num(a, "value")
  tol <- num(a, "tol", FALSE) %||% 0.01
  reported <- num(a, "reported", FALSE)
  if (stat == "F") {
    df1 <- num(a, "df1"); df2 <- num(a, "df2")
    es <- list(eta2_partial = (df1 * abs(x)) / (df1 * abs(x) + df2),
               omega2_partial = (df1 * (abs(x) - 1)) / (df1 * abs(x) + df2 + 1))
    return(list(
      formula = "eta2p = df1*F / (df1*F + df2); omega2p = df1*(F - 1) / (df1*F + df2 + 1)",
      engine = "reimplemented from metacheck inst/modules/stat_effect_size.R (classify_f_coherence)",
      result = c(es, list(comparison = compare(reported, es["eta2_partial"], tol),
                          note = "non-partial eta squared cannot be reconstructed from F and dfs in factorial/repeated designs"))))
  }
  if (stat != "t") stop("--stat must be t or F")
  design <- a$design %||% stop("Missing required argument: --design (within|between); the design must be stated, not guessed")
  abs_t <- abs(x)
  if (design == "within") {
    n <- num(a, "n", FALSE) %||% (num(a, "df") + 1)
    es <- list(dz = abs_t / sqrt(n), drm_r05 = (abs_t / sqrt(n)) / sqrt(1 - 0.5))
    f <- "dz = |t| / sqrt(n), n = df + 1; drm_r05 = dz / sqrt(1 - r) assuming r = .5"
    extra <- list(n = n, comparison = compare(reported, es, tol))
  } else if (design == "between") {
    n1 <- num(a, "n1", FALSE); n2 <- num(a, "n2", FALSE)
    if (!is.null(n1) && !is.null(n2)) {
      es <- list(ds = abs_t * sqrt(1 / n1 + 1 / n2))
      f <- "ds = |t| * sqrt(1/n1 + 1/n2)"
      extra <- list(n1 = n1, n2 = n2, comparison = compare(reported, es, tol))
    } else {
      df <- num(a, "df")
      if (abs(df - round(df)) > 1e-8) stop("Non-integer df indicates Welch's t-test; supply --n1 and --n2")
      N <- df + 2
      es <- list(ds_equal_n = 2 * abs_t / sqrt(N))
      if (N >= 4) {
        g1 <- 2:(N - 2)
        d_vals <- abs_t * sqrt(1 / g1 + 1 / (N - g1))
        es$ds_unequal_min <- min(d_vals); es$ds_unequal_max <- max(d_vals)
      }
      f <- "ds_equal_n = 2|t| / sqrt(df + 2); unequal-n range = |t| * sqrt(1/n1 + 1/n2) over all splits of N = df + 2 with n1, n2 >= 2"
      cmp <- compare(reported, es["ds_equal_n"], tol)
      if (!is.null(cmp) && !is.null(es$ds_unequal_min)) {
        cmp$in_unequal_n_range <- abs(reported) >= es$ds_unequal_min - tol && abs(reported) <= es$ds_unequal_max + tol
        if (cmp$in_unequal_n_range) {
          best <- which.min(abs(d_vals - abs(reported)))
          cmp$closest_split <- list(n1 = g1[best], n2 = N - g1[best])
        }
      }
      extra <- list(N = N, comparison = cmp, note = "group sizes not supplied: equal-n value plus the range over all unequal splits")
    }
  } else {
    stop("--design must be within or between")
  }
  list(formula = f,
       engine = "reimplemented from metacheck inst/modules/stat_effect_size.R (classify_d_coherence)",
       result = c(es, extra))
}

op_power <- function(a) {
  test <- a$test %||% stop("Missing required argument: --test")
  es <- num(a, "es")
  n <- num(a, "n", FALSE); pw <- num(a, "power", FALSE)
  if (is.null(n) == is.null(pw)) stop("Supply exactly one of --n (-> achieved power) or --power (-> required n)")
  alpha <- num(a, "alpha", FALSE) %||% 0.05
  tails <- num(a, "tails", FALSE) %||% 2
  rep_n <- num(a, "reported_n", FALSE)
  has_pwr <- requireNamespace("pwr", quietly = TRUE)
  alt <- if (tails == 1) "one.sided" else "two.sided"
  unit <- "per group"
  if (test %in% c("t_one", "t_paired", "t_two")) {
    type <- c(t_one = "one.sample", t_paired = "paired", t_two = "two.sample")[[test]]
    if (test != "t_two") unit <- if (test == "t_paired") "pairs" else "total"
    if (has_pwr) {
      r <- pwr::pwr.t.test(n = n, d = es, sig.level = alpha, power = pw, type = type,
                           alternative = if (tails == 1) "greater" else "two.sided")
      engine <- paste("pwr::pwr.t.test", utils::packageVersion("pwr"))
    } else {
      r <- stats::power.t.test(n = n, delta = abs(es), sd = 1, sig.level = alpha, power = pw,
                               type = type, alternative = alt, strict = TRUE)
      engine <- "stats::power.t.test (delta = d, sd = 1, strict = TRUE); pwr not installed"
    }
    res <- list(n = r$n, power = r$power)
    f <- "noncentral t; for t_paired, es is dz"
  } else if (test == "anova") {
    k <- num(a, "k")
    if (has_pwr) {
      r <- pwr::pwr.anova.test(k = k, n = n, f = es, sig.level = alpha, power = pw)
      engine <- paste("pwr::pwr.anova.test", utils::packageVersion("pwr"))
    } else {
      # power.anova.test: lambda = (k-1) * n * between.var / within.var; Cohen: lambda = k * n * f^2
      r <- stats::power.anova.test(groups = k, n = n, between.var = es^2 * k / (k - 1), within.var = 1,
                                   sig.level = alpha, power = pw)
      engine <- "stats::power.anova.test (between.var = f^2 * k/(k-1), within.var = 1); pwr not installed"
    }
    res <- list(n = r$n, power = r$power, k = k)
    f <- "noncentral F, lambda = k * n * f^2; es is Cohen's f; one-way between-subjects, balanced"
  } else if (test == "r") {
    unit <- "total"
    if (has_pwr) {
      r <- pwr::pwr.r.test(n = n, r = es, sig.level = alpha, power = pw,
                           alternative = if (tails == 1) "greater" else "two.sided")
      res <- list(n = r$n, power = r$power)
      engine <- paste("pwr::pwr.r.test", utils::packageVersion("pwr"))
      f <- "pwr.r.test"
    } else {
      # APPROXIMATION: Fisher z, atanh(r) ~ N(atanh(rho), 1/(n-3))
      za <- stats::qnorm(1 - alpha / tails); zr <- atanh(abs(es))
      if (is.null(n)) {
        res <- list(n = ((za + stats::qnorm(pw)) / zr)^2 + 3, power = pw)
      } else {
        res <- list(n = n, power = stats::pnorm(zr * sqrt(n - 3) - za))
      }
      engine <- "APPROXIMATION: Fisher z normal approximation (base R); pwr not installed. Can differ from exact by ~1-2 participants"
      f <- "n = ((z_(1-alpha/tails) + z_power) / atanh(r))^2 + 3; power = pnorm(atanh(r) * sqrt(n - 3) - z_(1-alpha/tails))"
    }
  } else {
    stop("--test must be one of t_one, t_paired, t_two, r, anova")
  }
  res$n_ceiling <- ceiling(res$n - 1e-9)
  res$n_unit <- unit
  if (test %in% c("t_two", "anova")) res$n_total <- res$n_ceiling * (if (test == "anova") res$k else 2)
  res$solved_for <- if (is.null(n)) "n" else "power"
  if (!is.null(rep_n)) {
    res$reported_n <- list(value = rep_n, unit = unit, required = res$n_ceiling,
                           meets_required = rep_n >= res$n_ceiling, difference = rep_n - res$n_ceiling)
  }
  list(formula = f, engine = engine, result = res)
}

op_convert <- function(a) {
  from <- a$from %||% stop("Missing required argument: --from (d|r)")
  x <- num(a, "value")
  if (from == "d") {
    list(formula = "r = d / sqrt(d^2 + 4)  (equal group sizes)", engine = "base R", result = list(r = x / sqrt(x^2 + 4)))
  } else if (from == "r") {
    if (abs(x) >= 1) stop("|r| must be < 1")
    list(formula = "d = 2r / sqrt(1 - r^2)  (equal group sizes)", engine = "base R", result = list(d = 2 * x / sqrt(1 - x^2)))
  } else {
    stop("--from must be d or r")
  }
}

`%||%` <- function(x, y) if (is.null(x)) y else x

mc_main({
  a <- mc_args()
  if (!is.null(a$json)) {
    j <- jsonlite::fromJSON(a$json, simplifyVector = TRUE)
    names(j) <- gsub("-", "_", names(j))
    a <- utils::modifyList(a[setdiff(names(a), "json")], j)
  }
  mc_require(a, "op")
  if (a$op == "list") {
    mc_out(list(status = "ok", op = "list", ops = OPS,
                note = "all flags can also be passed as --json '{\"op\":..., \"flag_name\":...}'"))
  } else {
    fn <- switch(a$op, p_from_stat = op_p_from_stat, statcheck = op_statcheck, implied_es = op_implied_es,
                 power = op_power, convert = op_convert,
                 stop("Unknown --op '", a$op, "'; use --op list"))
    r <- fn(a)
    mc_out(list(status = "ok", op = a$op, inputs = a[setdiff(names(a), "op")],
                formula = r$formula, engine = r$engine, result = r$result))
  }
})
