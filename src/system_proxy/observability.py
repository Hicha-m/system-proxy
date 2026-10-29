"""P2: private, offline SQLite observability and static HTML dashboard.

No server, active probe, Cloudflare request or raw proxy address is emitted.
Queries are read-only and only fixed, aggregated columns are exposed.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .persistence import data_directory

REFERENCE = "https://example.com/"
SOURCE_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,39}\Z")
CATEGORIES = {
    "success": "success", "proxy_error": "proxy_error", "unknown": "unknown",
    "network_error": "network_error", "target_denied": "target_denied",
    "rate_limited": "rate_limited", "challenge": "challenge",
    "passed": "passed", "failed": "failed", "unavailable": "unavailable",
    "connected": "connected", "connect_error": "connect_error",
    "denied": "denied",
}
REASONS = frozenset({
    "ok", "timeout", "tls_error", "dns_resolution_failed",
    "network_policy_denied", "proxy_connection_error", "proxy_or_target_denied",
    "connection_error", "invalid_response", "network", "handshake", "policy",
    "unknown", "target_unavailable", "connection_refused",
})


def _safe_category(value: str) -> str:
    return CATEGORIES.get(value, "other")


def _safe_reason(value: str) -> str:
    return value if value in REASONS else "other"


def _counts(rows, sanitize=_safe_category) -> dict[str, int]:
    counts = Counter()
    for name, count in rows:
        counts[sanitize(name)] += count
    return dict(sorted(counts.items()))


def _has(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def load_observability(db: Path, *, days: int = 14,
                       now: datetime | None = None) -> dict:
    """Aggregate public operational categories without leaking sensitive rows."""
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware clock required")
    current = now.astimezone(timezone.utc)
    report = {
        "status": "no_database", "window_days": days,
        "generated_at": current.isoformat(), "database_bytes": 0,
        "inventory_total": 0, "inventory_by_protocol": {},
        "daily_runs": [], "reference_outcomes": {}, "checks_by_status": {},
        "check_reasons": {}, "sources": [],
        "destination_outcomes": {}, "target_outcomes": {},
        "gateway_outcomes": {}, "alerts": [], "network_requests": 0,
        "privacy": "aggregate_only_no_endpoint_addresses_or_domains",
    }
    path = Path(db)
    if path.is_symlink():
        raise ValueError("Refusing symlinked database")
    if not path.is_file():
        return report
    cutoff = (current - timedelta(days=days)).isoformat()
    report["database_bytes"] = path.stat().st_size
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=4) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=4000")
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "endpoints" not in tables:
                report["status"] = "invalid_schema"
                return report
            report["status"] = "ok"
            report["inventory_total"] = conn.execute(
                "SELECT COUNT(*) FROM endpoints"
            ).fetchone()[0]
            report["inventory_by_protocol"] = _counts(conn.execute(
                "SELECT protocol, COUNT(*) FROM endpoints GROUP BY protocol"
            ))
            if "daily_runs" in tables:
                report["daily_runs"] = [
                    {"day": day, "collected": collected, "new": new,
                     "attempted": attempted, "passed": passed, "failed": failed}
                    for day, collected, new, attempted, passed, failed in
                    conn.execute("""
                        SELECT day,collected,new_candidates,attempted,passed,failed
                        FROM daily_runs ORDER BY day DESC LIMIT ?
                    """, (days,))
                ]
            if "url_checks" in tables:
                report["reference_outcomes"] = _counts(conn.execute("""
                    SELECT outcome,COUNT(*) FROM url_checks
                    WHERE checked_at>=? AND target_url=?
                    GROUP BY outcome
                """, (cutoff, REFERENCE)))
            if "checks" in tables:
                report["checks_by_status"] = _counts(conn.execute("""
                    SELECT status, COUNT(*) FROM checks
                    WHERE checked_at>=? GROUP BY status
                """, (cutoff,)))
                report["check_reasons"] = _counts(conn.execute("""
                    SELECT reason, COUNT(*) FROM checks
                    WHERE checked_at>=? GROUP BY reason
                """, (cutoff,)), sanitize=_safe_reason)
            if "provenance" in tables:
                # Source strings are untrusted. Only expose safe short identifiers.
                names = Counter()
                for name, count in conn.execute(
                    "SELECT source,COUNT(*) FROM provenance GROUP BY source"
                ):
                    if (isinstance(name, str) and SOURCE_NAME.fullmatch(name)
                            and not any(part in name.lower() for part in (
                                "secret", "token", "password", "apikey", "api_key"
                            ))):
                        names[name] += count
                    else:
                        names["other"] += count
                report["sources"] = [
                    {"source": name, "endpoints": count}
                    for name, count in sorted(names.items(), key=lambda x: (-x[1], x[0]))[:12]
                ]
            for table, column, time_col, output in (
                ("destination_observations", "outcome", "observed_at", "destination_outcomes"),
                ("target_observations", "outcome", "observed_at", "target_outcomes"),
                ("gateway_attempts", "outcome", "attempted_at", "gateway_outcomes"),
            ):
                if table in tables:
                    # SQL identifiers above are fixed in code, never supplied by a client.
                    report[output] = _counts(conn.execute(
                        f"SELECT {column},COUNT(*) FROM {table} "
                        f"WHERE {time_col}>=? GROUP BY {column}", (cutoff,)
                    ))
    except sqlite3.DatabaseError as exc:
        raise ValueError("Cannot read the SQLite observability schema") from exc

    recent = report["daily_runs"]
    if not recent:
        report["alerts"].append("no_daily_campaign_record")
    else:
        try:
            last = datetime.fromisoformat(recent[0]["day"]).date()
            if (current.date() - last).days > 2:
                report["alerts"].append("daily_campaign_stale")
        except (ValueError, TypeError):
            report["alerts"].append("invalid_daily_timestamp")
    attempts = sum(row["attempted"] for row in recent)
    failures = sum(row["failed"] for row in recent)
    if attempts >= 20 and failures / attempts >= 0.80:
        report["alerts"].append("high_daily_failure_rate")
    return report


def _table(rows: list[tuple[str, str]], headings=("Indicateur", "Valeur")) -> str:
    head = "".join("<th scope='col'>" + html.escape(str(x)) + "</th>" for x in headings)
    body = "".join(
        "<tr><th scope='row'>" + html.escape(str(key)) + "</th><td>" +
        html.escape(str(value)) + "</td></tr>"
        for key, value in rows
    ) or "<tr><td colspan='2'>Aucune donnée</td></tr>"
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>"


def render_dashboard(report: dict) -> str:
    """Pure static HTML: no JS, CDNs, network, endpoint identifiers or secrets."""
    esc = lambda value: html.escape(str(value), quote=True)
    inventory = _table(list(report["inventory_by_protocol"].items()),
                       ("Protocole", "Candidats"))
    reference = _table(list(report["reference_outcomes"].items()),
                       ("Résultat HTTPS de référence", "Contrôles"))
    reasons = _table(list(report["check_reasons"].items()),
                     ("Diagnostic", "Contrôles"))
    destinations = _table(list(report["destination_outcomes"].items()),
                          ("Observation passive", "Total"))
    sources = _table([(x["source"], x["endpoints"]) for x in report["sources"]],
                     ("Source", "Candidats référencés"))
    daily_rows = _table([
        (item["day"], f'{item["attempted"]} tentatives · {item["passed"]} succès · {item["failed"]} échecs')
        for item in report["daily_runs"]
    ], ("Jour UTC", "Campagne"))
    alerts = "".join("<li>" + esc(x) + "</li>" for x in report["alerts"])
    if not alerts:
        alerts = "<li>Aucune alerte de seuil</li>"
    # Strict meta CSP and no external assets (works offline, including file://).
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'">
<title>system-proxy · Observabilité locale</title>
<style>
:root{{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#0b1220;color:#e6edf7}}
*{{box-sizing:border-box}}body{{margin:0 auto;max-width:1160px;padding:40px 24px 70px}}
header{{border-bottom:1px solid #28374e;padding-bottom:22px;margin-bottom:24px}}
h1{{font-size:clamp(24px,3vw,38px);letter-spacing:-.035em;margin:0}}
h2{{font-size:17px;margin:0 0 18px;color:#f2f5ff}}p,small{{color:#aebdd1}}
.tag{{display:inline-block;border:1px solid #355778;border-radius:20px;padding:5px 12px;color:#83c8ef;font-size:12px;letter-spacing:.04em;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}
.panel{{background:#141f30;border:1px solid #2c3b50;border-radius:14px;padding:23px;overflow:auto}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:20px}}
.kpi{{padding:18px 20px;border-radius:13px;background:#19283b;border:1px solid #30435b}}
.kpi strong{{font-size:29px;display:block;margin-top:8px;font-variant-numeric:tabular-nums}}
.kpi span{{font-size:12px;color:#aebdd1}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:11px 5px;border-bottom:1px solid #2c3b50;text-align:left}}
td{{text-align:right;font-variant-numeric:tabular-nums}}thead th{{color:#9facc0;font-weight:500}}
ul{{padding-left:20px;color:#eabf83}}li{{margin-bottom:6px}}footer{{margin-top:26px;font-size:12px;color:#aebdd1}}
</style></head><body>
<header><span class="tag">P2 · PRIVÉ · HORS LIGNE</span>
<h1>Observabilité system-proxy</h1>
<p>Fenêtre des mesures : {esc(report["window_days"])} jours · Généré en UTC : {esc(report["generated_at"])} · État : {esc(report["status"])}</p></header>
<section class="kpis">
<div class="kpi"><span>PROXIES INVENTORIÉS</span><strong>{esc(report["inventory_total"])}</strong></div>
<div class="kpi"><span>BASE SQLITE</span><strong>{esc(round(report["database_bytes"] / (1024**2), 2))} Mo</strong></div>
<div class="kpi"><span>CONTRÔLES DE RÉFÉRENCE</span><strong>{esc(sum(report["reference_outcomes"].values()))}</strong></div>
<div class="kpi"><span>ALERTES</span><strong>{esc(len(report["alerts"]))}</strong></div>
</section>
<div class="grid">
<section class="panel"><h2>Inventaire par protocole</h2>{inventory}</section>
<section class="panel"><h2>Vérifications de référence</h2>{reference}</section>
<section class="panel"><h2>Erreurs et diagnostics</h2>{reasons}</section>
<section class="panel"><h2>Accès par destination</h2>{destinations}</section>
<section class="panel"><h2>Provenance des proxies</h2>{sources}</section>
<section class="panel"><h2>Historique des campagnes</h2>{daily_rows}</section>
<section class="panel"><h2>Alertes opérationnelles</h2><ul>{alerts}</ul></section>
<section class="panel"><h2>Autres mesures</h2>{_table([
    ("Projets (observations)", sum(report["target_outcomes"].values())),
    ("Passerelle CONNECT (tentatives)", sum(report["gateway_outcomes"].values())),
    ("Tests locaux", sum(report["checks_by_status"].values())),
])}</section>
</div><footer>Aucune IP, URL cible, donnée de requête ni secret exporté. Aucun serveur, script ou appel réseau. Les métriques sont des agrégats locaux et ne prouvent pas l’accessibilité des autres sites.</footer>
</body></html>"""


def write_private(path: Path, contents: str) -> None:
    if path.is_symlink():
        raise ValueError("Refusing symlinked output")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, staging = tempfile.mkstemp(prefix=".p2-report-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    finally:
        Path(staging).unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Private, offline P2 observability")
    parser.add_argument("--db", type=Path, default=data_directory() / "campaign.sqlite3")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--format", choices=("json", "html"), default="json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = load_observability(args.db, days=args.days)
        if args.format == "html" and args.output is None:
            raise ValueError("HTML output requires --output")
        content = (render_dashboard(report) if args.format == "html"
                   else json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if args.output is not None:
            write_private(args.output, content + "\n")
            print(json.dumps({"status": "written", "format": args.format}))
        else:
            print(content)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
