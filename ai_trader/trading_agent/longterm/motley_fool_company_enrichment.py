"""Motley Fool company-page enrichment for long-term research ideas."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_PROFILE_DIR = Path.home() / ".grok3api_chrome_profile"
STANDARD_SECTIONS = (
    "market_snapshot",
    "current_recommendation",
    "company_overview",
    "premium_coverage",
    "external_news",
    "moneyball_scores",
    "growth_metrics",
    "valuation_metrics",
    "profitability_metrics",
    "financials_ttm",
    "recent_earnings",
    "bull_cases",
    "bear_cases",
)
FOOTER_LABELS = {
    "Fool Disclosure",
    "Privacy Policy",
    "Terms and Conditions",
    "Accessibility Policy",
    "Copyright, Trademark and Patent information",
}
EARNINGS_STOP_LABELS = {
    *FOOTER_LABELS,
    "Upcoming Earnings",
    "View Full Earnings Report",
    "Watch & Listen",
    "Potential Bull Cases",
    "Potential Bear Cases",
    "Premium Coverage",
    "Revenue Growth (CAGR)",
    "Valuation",
    "Profitability",
    "Financials",
}
EARNINGS_SUBHEADINGS = {"Q1 Earnings", "Q2 Earnings", "Q3 Earnings", "Q4 Earnings"}


@dataclass
class CompanyPageSnapshot:
    """Provider-neutral snapshot of a Motley Fool company page."""

    requested_url: str
    resolved_url: str = ""
    title: str = ""
    text: str = ""
    headings: list[str] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    captured_at: str = ""

    def __post_init__(self) -> None:
        self.requested_url = str(self.requested_url or "")
        self.resolved_url = str(self.resolved_url or self.requested_url)
        self.title = str(self.title or "")
        self.text = str(self.text or "")
        self.headings = [str(item) for item in (self.headings or [])]
        self.tables = [dict(item) for item in (self.tables or [])]
        self.links = [dict(item) for item in (self.links or [])]
        self.captured_at = self.captured_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CompanyPageSnapshot":
        return cls(**{key: payload.get(key) for key in cls.__dataclass_fields__})


def enrich_idea_from_company_snapshot(
    idea: Mapping[str, Any],
    snapshot: CompanyPageSnapshot | Mapping[str, Any],
) -> dict[str, Any]:
    """Merge a Motley Fool company snapshot into a research idea."""
    snap = snapshot if isinstance(snapshot, CompanyPageSnapshot) else CompanyPageSnapshot.from_dict(snapshot)
    payload = dict(idea)
    symbol = str(payload.get("symbol") or _symbol_from_snapshot(snap) or "").upper()
    payload["symbol"] = symbol
    if not payload.get("company_name"):
        payload["company_name"] = _company_name_from_snapshot(snap) or symbol

    text_lines = _clean_lines(snap.text)
    moneyball = _parse_moneyball(text_lines)
    metric_tables = _parse_metric_tables(snap.tables)
    market_snapshot = _parse_market_snapshot(text_lines)
    recommendation = _parse_current_recommendation(text_lines, symbol=symbol)
    overview = _parse_company_overview(text_lines, symbol=symbol)
    recent_earnings = _parse_recent_earnings(text_lines, snap.links, symbol=symbol)
    bull_cases = _parse_numbered_section(text_lines, "Potential Bull Cases")
    bear_cases = _parse_numbered_section(text_lines, "Potential Bear Cases")
    premium_coverage = _parse_premium_coverage(text_lines, snap.links)
    external_news = _parse_external_news(text_lines, symbol=symbol)

    sections = {
        "market_snapshot": market_snapshot,
        "current_recommendation": recommendation,
        "company_overview": overview,
        "premium_coverage": premium_coverage,
        "external_news": external_news,
        "moneyball_scores": moneyball,
        **metric_tables,
        "recent_earnings": recent_earnings,
        "bull_cases": bull_cases,
        "bear_cases": bear_cases,
    }
    sections_found = [
        name
        for name in STANDARD_SECTIONS
        if _section_present(name, sections.get(name))
    ]
    sections_missing = [name for name in STANDARD_SECTIONS if name not in sections_found]

    enrichment = {
        "source": "motley_fool_company_page",
        "requested_url": snap.requested_url,
        "resolved_url": snap.resolved_url,
        "title": snap.title,
        "captured_at": snap.captured_at,
        "sections_found": sections_found,
        "sections_missing": sections_missing,
        **sections,
        "source_links": _source_links(snap.links),
    }
    payload["motley_fool_company_enrichment"] = enrichment

    if overview.get("summary"):
        payload["business_summary"] = overview["summary"]
    elif not payload.get("business_summary"):
        payload["business_summary"] = _fallback_business_summary(payload, recent_earnings)

    thesis = _build_thesis_summary(recommendation, recent_earnings, bull_cases, bear_cases)
    if thesis:
        payload["thesis_summary"] = thesis
    driver = _primary_growth_driver(recommendation, bull_cases, recent_earnings)
    if driver:
        payload["primary_growth_driver"] = driver
    if overview.get("sector") or overview.get("industry"):
        payload["industry_context"] = _join_nonempty(
            [
                f"Sector: {overview.get('sector')}" if overview.get("sector") else "",
                f"Industry: {overview.get('industry')}" if overview.get("industry") else "",
            ],
            sep="; ",
        )

    balance_sheet = _balance_sheet_assessment(metric_tables)
    if balance_sheet:
        payload["balance_sheet_assessment"] = balance_sheet
    if moneyball.get("quality_pct") is not None:
        payload["quality_score"] = moneyball["quality_pct"]
    if moneyball.get("valuation_pct") is not None:
        payload["valuation_score"] = moneyball["valuation_pct"]

    payload["confirming_signals"] = _dedupe(
        [*payload.get("confirming_signals", []), *_confirming_signals(recommendation, recent_earnings, moneyball)]
    )
    payload["invalidation_conditions"] = _dedupe(
        [
            *payload.get("invalidation_conditions", []),
            *_risk_signals(recommendation, recent_earnings, moneyball, metric_tables, bear_cases),
        ]
    )
    notes = [str(note) for note in (payload.get("source_notes") or []) if str(note).strip()]
    notes.extend(
        [
            f"Motley Fool company enrichment URL: {snap.resolved_url}.",
            f"Motley Fool sections found: {', '.join(sections_found) or 'none'}.",
        ]
    )
    payload["source_notes"] = _dedupe(notes)
    return payload


def enrich_ideas_with_company_pages(
    ideas: list[Mapping[str, Any]],
    *,
    fetch_snapshot: Callable[[Mapping[str, Any]], CompanyPageSnapshot],
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch and enrich a batch of Motley Fool ideas."""
    enriched: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    processed = 0
    for idea in ideas:
        if limit is not None and processed >= limit:
            enriched.append(dict(idea))
            continue
        url = str(idea.get("motley_fool_company_url") or idea.get("source_url") or "").strip()
        if not url:
            item = dict(idea)
            item.setdefault("motley_fool_company_enrichment_error", "missing_company_url")
            enriched.append(item)
            errors.append({"symbol": str(idea.get("symbol") or ""), "error": "missing_company_url"})
            continue
        try:
            enriched.append(enrich_idea_from_company_snapshot(idea, fetch_snapshot(idea)))
            processed += 1
        except Exception as exc:  # pragma: no cover - CLI safety path
            item = dict(idea)
            item["motley_fool_company_enrichment_error"] = f"{type(exc).__name__}: {exc}"
            enriched.append(item)
            errors.append({"symbol": str(idea.get("symbol") or ""), "error": item["motley_fool_company_enrichment_error"]})
    return enriched, {
        "input_count": len(ideas),
        "enriched_count": processed,
        "skipped_count": len(ideas) - processed,
        "error_count": len(errors),
        "errors": errors,
    }


def fetch_company_snapshot_with_scrapling(
    url: str,
    *,
    profile_dir: str | Path | None = None,
    headless: bool = True,
    backend: str = "scrapling_stealthy",
    timeout_ms: int = 90000,
) -> CompanyPageSnapshot:
    """Fetch a Motley Fool company page using Scrapling.

    Scrapling is optional. The import is lazy so normal repo tests do not require
    the scraping dependency.
    """
    try:
        from scrapling.fetchers import DynamicSession, StealthySession  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("scrapling[fetchers] is required for Motley Fool company enrichment") from exc

    session_cls = StealthySession if backend == "scrapling_stealthy" else DynamicSession

    with session_cls(
        headless=headless,
        real_chrome=True,
        user_data_dir=str(profile_dir or DEFAULT_PROFILE_DIR),
        network_idle=False,
        timeout=timeout_ms,
        page_action=lambda page: _wait_for_company_content(page, timeout_ms=timeout_ms),
        disable_resources=False,
        google_search=False,
        **({"solve_cloudflare": False} if session_cls is StealthySession else {}),
    ) as session:
        page = session.fetch(url)
        return snapshot_from_scrapling_page(page, requested_url=url)


def snapshot_from_scrapling_page(page: Any, *, requested_url: str) -> CompanyPageSnapshot:
    """Create a provider-neutral snapshot from a Scrapling response."""
    return CompanyPageSnapshot(
        requested_url=requested_url,
        resolved_url=str(getattr(page, "url", "") or requested_url),
        title=_selector_get(page.css("title::text")),
        text=page.get_all_text(strip=True) if hasattr(page, "get_all_text") else "",
        headings=page.css("h1::text, h2::text, h3::text").getall(),
        tables=_tables_from_scrapling_page(page),
        links=_links_from_scrapling_page(page),
    )


def _wait_for_company_content(page: Any, *, timeout_ms: int) -> None:
    """Wait for initial Fool IQ content, then scroll to trigger lazy sections."""
    page.wait_for_function(
        """
        () => {
            const text = (document.body?.innerText || '').replace(/\u200c/g, '').trim();
            return document.querySelectorAll('table').length > 0 || text.includes('Hidden Gems - Moneyball');
        }
        """,
        timeout=min(timeout_ms, 60000),
    )
    for _ in range(6):
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(500)


def _tables_from_scrapling_page(page: Any) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for table in page.css("table"):
        headers = [_clean_text(item.get_all_text(strip=True)) for item in table.css("thead th")]
        rows = []
        row_links = []
        for row in table.css("tbody tr"):
            cells = row.css("td,th")
            cell_texts = [_clean_text(cell.get_all_text(strip=True)) for cell in cells]
            if not any(cell_texts):
                row_text = _clean_text(row.get_all_text(strip=True))
                cell_texts = [part.strip() for part in row_text.split("\t") if part.strip()]
            if not any(cell_texts):
                continue
            links = []
            for cell in cells:
                anchors = cell.css("a[href]")
                href = anchors[0].attrib.get("href", "") if anchors else ""
                links.append(_absolute_url(page, href))
            rows.append(cell_texts)
            row_links.append(links)
        if rows:
            tables.append({"title": "", "headers": headers, "rows": rows, "row_links": row_links})
    return tables


def _links_from_scrapling_page(page: Any) -> list[dict[str, str]]:
    links = []
    for anchor in page.css("a[href]"):
        href = str(anchor.attrib.get("href", "") or "")
        text = _clean_text(anchor.get_all_text(strip=True))
        if href:
            links.append({"text": text, "href": _absolute_url(page, href)})
    return links


def _parse_moneyball(lines: list[str]) -> dict[str, Any]:
    labels = {
        "Superscore": "superscore",
        "Finance 1Y": "finance_1y",
        "Finance 5Y": "finance_5y",
        "Product 1Y": "product_1y",
        "Product 5Y": "product_5y",
        "Leaders": "leaders",
        "AI": "ai",
        "Quant: 5Y": "quant_5y",
        "Investing Type": "investing_type",
        "Est. Annualized Return": "estimated_annualized_return",
        "Est. Max Drawdown": "estimated_max_drawdown",
        "Quality": "quality_pct",
        "Growth": "growth_pct",
        "Valuation": "valuation_pct",
        "Safety": "safety_pct",
        "Market Buzz": "market_buzz_pct",
    }
    data: dict[str, Any] = {}
    for label, key in labels.items():
        value = _next_value_after(lines, label)
        if not value:
            continue
        if key.endswith("_pct") or key in {
            "superscore",
            "finance_1y",
            "finance_5y",
            "product_1y",
            "product_5y",
            "leaders",
            "ai",
            "quant_5y",
        }:
            data[key] = _parse_number_or_none(value)
        else:
            data[key] = None if value.upper() in {"N/A", "—", "-"} else value
    return data


def _parse_metric_tables(tables: list[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    parsed = {
        "growth_metrics": {},
        "valuation_metrics": {},
        "profitability_metrics": {},
        "financials_ttm": {},
    }
    for table in tables:
        rows = table.get("rows") or []
        metrics = {
            _clean_text(row[0]): _clean_text(row[1])
            for row in rows
            if isinstance(row, list) and len(row) >= 2 and _clean_text(row[0])
        }
        keys = set(metrics)
        if "3-Yr Revenue Growth" in keys:
            parsed["growth_metrics"].update(_normalize_metric_keys(metrics))
        elif "Price/Earnings" in keys:
            parsed["valuation_metrics"].update(_normalize_metric_keys(metrics))
        elif "Gross Margin" in keys:
            parsed["profitability_metrics"].update(_normalize_metric_keys(metrics))
        elif "Revenue" in keys and ("Total Debt" in keys or "Free Cash Flow" in keys):
            parsed["financials_ttm"].update(_normalize_metric_keys(metrics))
    return parsed


def _parse_market_snapshot(lines: list[str]) -> dict[str, Any]:
    labels = [
        "Market Cap",
        "52 Week Range",
        "Daily Range",
        "Volume",
        "Avg. Volume",
        "Beta",
        "Dividend Yield",
        "Insider Ownership",
        "Next Earnings Date",
    ]
    data = {}
    for label in labels:
        value = _market_snapshot_value_after(lines, label)
        if value:
            data[_metric_key(label)] = value
    return data


def _parse_current_recommendation(lines: list[str], *, symbol: str) -> dict[str, Any]:
    action_index = _first_index_matching(lines, rf"^(Buy|Sell|Hold|Reduce|Add)\s+{re.escape(symbol)}\b")
    if action_index is None:
        return {}
    stop_labels = {"Article Summary by AI", "Read Recommendation", f"{symbol} Company Overview"}
    bullets = []
    for line in lines[action_index + 3 :]:
        if line in stop_labels or line.startswith("Photo of "):
            break
        if len(line) > 20 and not re.match(r"^[A-Za-z .,'-]+$", line):
            bullets.append(line)
        elif line.endswith(".") and len(line) > 20:
            bullets.append(line)
    return {
        "action": lines[action_index].split()[0],
        "service": lines[action_index + 1] if action_index + 1 < len(lines) else "",
        "date": lines[action_index + 2] if action_index + 2 < len(lines) else "",
        "bullets": bullets[:5],
    }


def _parse_company_overview(lines: list[str], *, symbol: str) -> dict[str, str]:
    heading = f"{symbol} Company Overview"
    index = _index_of(lines, heading)
    if index is None:
        return {}
    summary = ""
    for line in lines[index + 1 :]:
        if line in {"Read More", "Sector"}:
            break
        if len(line) > 20:
            summary = line
            break
    return {
        "summary": summary,
        "sector": _next_value_after(lines[index:], "Sector"),
        "industry": _next_value_after(lines[index:], "Industry"),
    }


def _parse_recent_earnings(lines: list[str], links: list[Mapping[str, str]], *, symbol: str) -> dict[str, Any]:
    index = _first_index_matching(lines, rf"^{re.escape(symbol)} Recent Earnings\b")
    if index is None:
        return {"present": False, "metrics": {}, "key_financial_takeaways": [], "latest_developments": []}
    window = _slice_until_stop(lines[index : index + 80], EARNINGS_STOP_LABELS)
    metrics = {}
    for label in ("EPS", "Revenue", "Operating Income", "Free Cash Flow"):
        value = _next_value_after(window, label)
        if value and not _is_noise_line(value) and not value.startswith("View Full"):
            metrics[_metric_key(label)] = None if value in {"—", "-"} else value
    article_title = ""
    for line in window:
        if _looks_like_earnings_article_title(line, symbol=symbol):
                article_title = line
                break
    summary = _line_after(window, article_title)
    if _is_noise_line(summary):
        summary = ""
    return {
        "present": True,
        "heading": lines[index],
        "subheading": window[1] if len(window) > 1 else "",
        "metrics": metrics,
        "article_title": article_title,
        "article_url": _first_link_containing(links, "earnings") or _first_link_text(links, "View Full Earnings Report"),
        "summary": summary,
        "key_financial_takeaways": _collect_until_label(window, "Key Financial Takeaways", {"Latest Developments", *EARNINGS_STOP_LABELS}),
        "latest_developments": _collect_until_label(window, "Latest Developments", EARNINGS_STOP_LABELS),
    }


def _parse_numbered_section(lines: list[str], label: str) -> list[str]:
    index = _index_of(lines, label)
    if index is None:
        return []
    items = []
    for line in lines[index + 1 :]:
        if line == "Read More" or line.startswith("Potential "):
            break
        cleaned = re.sub(r"^\d+\.\s*", "", line).strip()
        if cleaned and len(cleaned) > 8:
            items.append(cleaned)
    return items[:8]


def _parse_premium_coverage(lines: list[str], links: list[Mapping[str, str]]) -> list[dict[str, str]]:
    index = _index_of(lines, "Premium Coverage")
    if index is None:
        return []
    coverage_links = [
        {"title": str(link.get("text") or ""), "url": str(link.get("href") or "")}
        for link in links
        if "/premium/" in str(link.get("href") or "") and str(link.get("text") or "").strip()
    ]
    return coverage_links[:10]


def _parse_external_news(lines: list[str], *, symbol: str) -> dict[str, Any]:
    index = _index_of(lines, "External News")
    if index is None:
        return {"present": False, "headlines": []}
    next_line = lines[index + 1] if index + 1 < len(lines) else ""
    if next_line == f"No external news available for {symbol}":
        return {"present": False, "headlines": []}
    return {"present": bool(next_line), "headlines": [next_line] if next_line else []}


def _section_present(name: str, value: Any) -> bool:
    if name == "recent_earnings":
        return bool(isinstance(value, Mapping) and value.get("present"))
    if name == "external_news":
        return bool(isinstance(value, Mapping) and value.get("present"))
    return bool(value)


def _build_thesis_summary(recommendation: Mapping[str, Any], earnings: Mapping[str, Any], bull: list[str], bear: list[str]) -> str:
    parts = []
    bullets = recommendation.get("bullets") or []
    if bullets:
        parts.append("Recommendation context: " + "; ".join(bullets[:3]))
    if bull:
        parts.append("Bull cases: " + "; ".join(bull[:3]))
    if earnings.get("article_title"):
        parts.append("Recent earnings theme: " + str(earnings.get("article_title")))
    if bear:
        parts.append("Bear cases: " + "; ".join(bear[:2]))
    return " ".join(parts)


def _primary_growth_driver(recommendation: Mapping[str, Any], bull: list[str], earnings: Mapping[str, Any]) -> str:
    candidates = [*(recommendation.get("bullets") or []), *bull, str(earnings.get("article_title") or "")]
    return next((item for item in candidates if item), "")


def _balance_sheet_assessment(metric_tables: Mapping[str, Mapping[str, str]]) -> str:
    financials = metric_tables.get("financials_ttm") or {}
    profitability = metric_tables.get("profitability_metrics") or {}
    parts = []
    for key, label in (("total_debt", "Total Debt"), ("total_cash", "Total Cash"), ("total_equity", "Total Equity")):
        if financials.get(key):
            parts.append(f"{label}: {financials[key]}")
    if profitability.get("debt_equity"):
        parts.append(f"Debt/Equity: {profitability['debt_equity']}")
    return "; ".join(parts)


def _confirming_signals(recommendation: Mapping[str, Any], earnings: Mapping[str, Any], moneyball: Mapping[str, Any]) -> list[str]:
    signals = []
    signals.extend(recommendation.get("bullets") or [])
    signals.extend(earnings.get("key_financial_takeaways") or [])
    signals.extend(earnings.get("latest_developments") or [])
    if moneyball.get("superscore") is not None:
        signals.append(f"Motley Fool Moneyball superscore: {moneyball['superscore']}.")
    return signals[:10]


def _risk_signals(
    recommendation: Mapping[str, Any],
    earnings: Mapping[str, Any],
    moneyball: Mapping[str, Any],
    metric_tables: Mapping[str, Mapping[str, str]],
    bear_cases: list[str],
) -> list[str]:
    risks = list(bear_cases[:5])
    risks.extend(earnings.get("latest_developments") or [])
    if moneyball.get("estimated_max_drawdown"):
        risks.append(f"Motley Fool estimated max drawdown: {moneyball['estimated_max_drawdown']}.")
    if moneyball.get("valuation_pct") is not None and float(moneyball["valuation_pct"]) < 25:
        risks.append(f"Low Motley Fool valuation score: {moneyball['valuation_pct']}%.")
    if moneyball.get("safety_pct") is not None and float(moneyball["safety_pct"]) < 35:
        risks.append(f"Low Motley Fool safety score: {moneyball['safety_pct']}%.")
    fcf = (metric_tables.get("financials_ttm") or {}).get("free_cash_flow", "")
    if str(fcf).startswith("-"):
        risks.append(f"Negative free cash flow: {fcf}.")
    return risks[:10]


def _source_links(links: list[Mapping[str, str]]) -> list[dict[str, str]]:
    keep = []
    for link in links:
        href = str(link.get("href") or "")
        text = str(link.get("text") or "")
        if href and ("/premium/" in href or "/investing/" in href):
            keep.append({"text": text, "href": href})
    return keep[:20]


def _normalize_metric_keys(metrics: Mapping[str, str]) -> dict[str, str]:
    return {_metric_key(key): value for key, value in metrics.items() if value not in {"", "—", "-"}}


def _metric_key(label: str) -> str:
    text = label.lower().replace("&", "and")
    text = text.replace("3-yr", "3_yr").replace("5-yr", "5_yr")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def _clean_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in str(text or "").replace("\u200c", "").splitlines()
        if line.strip()
    ]


def _next_value_after(lines: list[str], label: str) -> str:
    index = _index_of(lines, label)
    if index is None:
        return ""
    for value in lines[index + 1 : index + 5]:
        if value and value != label:
            return value
    return ""


def _market_snapshot_value_after(lines: list[str], label: str) -> str:
    index = _index_of(lines, label)
    if index is None:
        return ""
    if label == "Market Cap":
        parts = [value for value in lines[index + 1 : index + 5] if value and value != label]
        if len(parts) >= 3 and parts[0] == "$" and re.fullmatch(r"-?\d+(?:\.\d+)?", parts[1]) and re.fullmatch(r"[KMBT]", parts[2], flags=re.I):
            return f"${parts[1]}{parts[2].upper()}"
    return _next_value_after(lines, label)


def _slice_until_stop(lines: list[str], stop_labels: set[str]) -> list[str]:
    result = []
    for line in lines:
        if _matches_any_stop_label(line, stop_labels):
            break
        result.append(line)
    return result


def _line_after(lines: list[str], label: str) -> str:
    index = _index_of(lines, label)
    if index is None:
        return ""
    return lines[index + 1] if index + 1 < len(lines) else ""


def _collect_until_label(lines: list[str], label: str, stop_labels: set[str]) -> list[str]:
    index = _index_of(lines, label)
    if index is None:
        return []
    items = []
    for line in lines[index + 1 :]:
        if _matches_any_stop_label(line, stop_labels):
            break
        if len(line) > 10 and not _is_noise_line(line):
            items.append(line)
    return items[:8]


def _looks_like_earnings_article_title(line: str, *, symbol: str) -> bool:
    if not line or len(line) <= 25 or _is_noise_line(line):
        return False
    if line in {"Key Financial Takeaways", "Latest Developments"} or line in EARNINGS_SUBHEADINGS:
        return False
    if line.startswith(symbol) or line.startswith("Revenue:") or line.startswith("Diluted"):
        return False
    if re.match(r"^(EPS|Revenue|Operating Income|Free Cash Flow|Announce Date)(\s|$)", line):
        return False
    if re.match(r"^[\d$().,%/\- ]+$", line):
        return False
    return True


def _matches_any_stop_label(line: str, stop_labels: set[str]) -> bool:
    return any(line == label or line.startswith(f"{label} ") for label in stop_labels)


def _is_noise_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return True
    return _matches_any_stop_label(text, FOOTER_LABELS) or text in {
        "View Full Earnings Report",
        "Read More",
    }


def _index_of(lines: list[str], label: str) -> int | None:
    try:
        return lines.index(label)
    except ValueError:
        return None


def _first_index_matching(lines: list[str], pattern: str) -> int | None:
    regex = re.compile(pattern, flags=re.I)
    for index, line in enumerate(lines):
        if regex.search(line):
            return index
    return None


def _first_link_containing(links: list[Mapping[str, str]], needle: str) -> str:
    needle = needle.lower()
    for link in links:
        href = str(link.get("href") or "")
        if needle in href.lower():
            return href
    return ""


def _first_link_text(links: list[Mapping[str, str]], text: str) -> str:
    text = text.lower()
    for link in links:
        if text in str(link.get("text") or "").lower():
            return str(link.get("href") or "")
    return ""


def _parse_number_or_none(value: str) -> float | None:
    value = str(value or "").strip()
    if value.upper() in {"N/A", "—", "-"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group(0)) if match else None


def _symbol_from_snapshot(snapshot: CompanyPageSnapshot) -> str:
    match = re.search(r"\(([A-Z]+):([A-Z.]+)\)", " ".join(snapshot.headings) or snapshot.text)
    return match.group(2) if match else ""


def _company_name_from_snapshot(snapshot: CompanyPageSnapshot) -> str:
    if snapshot.headings:
        return re.sub(r"\([^)]*\)", "", snapshot.headings[0]).strip()
    return ""


def _fallback_business_summary(payload: Mapping[str, Any], earnings: Mapping[str, Any]) -> str:
    if earnings.get("summary"):
        return str(earnings["summary"])
    return str(payload.get("company_name") or payload.get("symbol") or "")


def _join_nonempty(values: list[str], *, sep: str = " ") -> str:
    return sep.join(value for value in values if value)


def _dedupe(values: list[Any]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _selector_get(selector: Any) -> str:
    try:
        return str(selector.get(default="") or "")
    except TypeError:
        return str(selector.get() or "")


def _absolute_url(page: Any, href: str) -> str:
    href = str(href or "")
    if not href:
        return ""
    try:
        return page.urljoin(href)
    except Exception:
        return href


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u200c", "").strip()


__all__ = [
    "CompanyPageSnapshot",
    "DEFAULT_PROFILE_DIR",
    "enrich_idea_from_company_snapshot",
    "enrich_ideas_with_company_pages",
    "fetch_company_snapshot_with_scrapling",
    "snapshot_from_scrapling_page",
]
