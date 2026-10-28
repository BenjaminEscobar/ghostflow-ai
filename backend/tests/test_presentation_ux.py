"""Final UX polish for the hackathon demo: the expandable browser viewer and the AI provenance
labelled stage by stage (interpretation and recovery are separate calls with separate sources)."""

import re

from ghostflow.console import STATIC_DIR
from ghostflow.evidence import AiSource, ai_badge, interpretation_badge


def _static():
    return tuple((STATIC_DIR / f).read_text(encoding="utf-8") for f in ["index.html", "console.js", "console.css"])


def test_sidebar_badges_name_the_stage_they_describe():
    real = ai_badge(AiSource(provider="meta", model="muse-spark-1.3", requests_sent=1))
    mock = ai_badge(AiSource(provider="mock", model="mock-deterministic"))
    configured = ai_badge(AiSource(provider="meta", model="muse-spark-1.3"))
    for b in [real, mock, configured, ai_badge(AiSource(provider="none"))]:
        assert b["text"].startswith("Recovery · "), b  # never a global "AI" label
    assert "0 Meta requests" in mock["text"] and "Real" not in mock["text"]
    assert "1 request" in real["text"] and "no request sent" in configured["text"]
    stored = interpretation_badge({"provider": "meta", "model": "muse-spark-1.3"})
    assert stored == {"kind": "real", "text": "Interpretation · Meta Muse Spark · muse-spark-1.3 · recorded"}
    assert interpretation_badge(None)["text"].startswith("Interpretation · none")
    assert interpretation_badge({"provider": "mock", "model": "m"})["text"].startswith("Interpretation · simulated")
    _, js, _ = _static()
    # The sidebar keeps the evidence's recovery badge; the interpretation has its own Demo chip.
    assert "badge(d.ai_badge.kind, d.ai_badge.text)" in js and 'fill($("prov-row")' in js


def test_provenance_is_derived_per_stage_and_never_invents_requests():
    html, js, _ = _static()
    demo, dev = html[html.index('id="demo-view"') : html.index('id="dev-view"')], html[html.index('id="dev-view"') :]
    assert 'id="prov-row"' in demo and 'id="prov-stages"' in dev and 'id="prov-note"' in dev
    stages = js[js.index("function interpretationStage(b)") : js.index("function stageBlock(")]
    # Each stage reads its own record: the workflow's interpretation, the step's recovery.
    assert "b.workflow.interpretation" in stages and "target.recovery" in stages
    assert '"Workflow interpretation"' in js and "`Self-Healing recovery · scenario ${sc.key}`" in js
    # A real recovery: recorded or saved evidence is never presented as a new request; the count
    # is shown only when the evidence stores it.
    assert "not a new request" in stages and "viewing it sends no new request" in stages
    assert 'const n = b.ai.provider === "meta" ? b.ai.requests_sent : 0;' in stages
    assert "n > 0 ?" in stages and "The number of requests is not recorded in this evidence." in stages
    # A mock recovery is simulated, even though the browser run (demo runner) is real.
    assert "0 Meta requests in this evidence." in stages
    assert 'b.origin === "demo_runner" ? "The browser run itself is real' in stages
    # Two different calls are never presented as one execution.
    assert "Two independent AI stages, not one execution" in stages and "model calls" not in stages
    assert "the recovery ${ran} was simulated by the offline mock." in stages
    assert "Neither stage sent a new Meta request." in stages and "the real Meta interpretation" in stages
    assert "when the workflow was compiled (GF-002)" in stages
    for invented in ["Math.random", "Date.now()", "tokens"]:
        assert invented not in stages, invented


def test_expanded_browser_is_an_accessible_dialog_around_the_same_viewer():
    html, js, css = _static()
    assert re.search(r'<dialog class="browser-dialog" id="browser-dialog" aria-labelledby="xb-title"', html)
    assert 'id="expand-browser" aria-haspopup="dialog" aria-controls="browser-dialog"' in html
    assert "Expand browser" in html and 'id="xb-close">Exit expanded view' in html
    # One status region per layout: the page behind a modal dialog is inert.
    assert '<p class="sr-only" id="xb-status" role="status" aria-live="polite"></p>' in html
    assert '$(state.expanded ? "xb-status" : "agent-status")' in js
    # Modal, Escape and the close button restore the layout and give focus back.
    assert "dialog.showModal()" in js and '$("browser-dialog").addEventListener("close", restoreLayout)' in js
    assert 'if (e.key === "Escape")' in js and '$("expand-browser").focus();' in js and '$("xb-close").focus();' in js
    assert '["ArrowLeft", "ArrowRight"]' in js and 'aria-disabled' in js
    # The viewer itself moves (nothing cloned, nothing rendered twice), so live captures stay in sync.
    for node in ["stage-col", "events", "film"]:
        assert f'moveInto($("{node}")' in js, node
    assert "cloneNode" not in js and "mark.replaceWith(node)" in js
    # The image is never upscaled beyond its own pixels and is kept within the screen.
    assert "min(100%, var(--shot-w, 100%), calc(var(--xb-img-h) * var(--shot-ratio, 1.6)))" in css
    assert '"--shot-w", `${c.width}px`' in js and ".browser-dialog::backdrop" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_demo_view_puts_the_browser_first_and_keeps_the_audit_in_developer():
    html, js, css = _static()
    demo = html[html.index('id="demo-view"') : html.index('id="dev-view"')]
    # A compact header band, then one heading line and the page progress, then the browser.
    order = [demo.index(x) for x in ['class="card hero"', 'id="sc-title"', 'id="flow"', 'class="sc-body"']]
    assert order == sorted(order)
    for gone in ['id="capabilities"', "How it works", 'id="how-meta"', 'id="understand"', 'id="ai-provenance"', 'id="sc-sub"', "side-foot"]:
        assert gone not in html, gone
    # The viewer's own tools live in its toolbar, so they travel with it into the expanded dialog.
    bar = demo[demo.index('class="browser-bar"') : demo.index('id="shot"')]
    for tool in ['id="stage-prov"', 'id="address"', 'id="view-toggle"', 'id="expand-browser"']:
        assert tool in bar, tool
    # The browser is the wide column; GhostFlow's state, Recovery and Verification sit beside it.
    body = demo[demo.index('class="sc-body"') :]
    assert body.index('id="stage-slot"') < body.index('class="sc-side"')
    assert body.index('id="mascot-say"') < body.index('id="heal-summary"') < body.index('id="verifs"') < body.index('id="steps"')
    assert ".sc-body { display: grid; grid-template-columns: minmax(0, 1fr) 288px;" in css
    assert ".stage-col > .events { display: none; }" in css  # the panel tells it once; the dialog lists the events
    for kept in ['id="mascot"', 'id="agent-status"', 'id="replay"', 'id="expand-browser"', 'id="film"', 'id="events"']:
        assert kept in demo, kept
    # Recovery shows the decision only; candidates, latency, tokens and confidence stay in Developer.
    heal = js[js.index("function renderHealSummary()") : js.index("// Interpretation (GF-002) is optional context")]
    for detail in ["candidates", "latency_ms", "tokens", "justification", "confidence"]:
        assert detail not in heal, detail
    for fact in ['"Original"', '"Selected"', '"Checks"', '"Source"', "passed`", '"View details"', '"Not needed"', "No recovery needed"]:
        assert fact in heal, fact
    assert '"Recorded recovery · selected evidence"' in heal and '"Live recovery · this run"' in heal
    # Brief scenario titles and states come from the scenario's own facts; screen readers and the
    # tooltip keep the full outcome and the stored title.
    assert 'sc.portal === "original" ? "Original"' in js and '"Changed UI"' in js and "title: cleanTitle(sc)" in js
    assert 'brief: failed ? "Stopped safely" : "Failed"' in js and 'brief: "Recovered"' in js and 'brief: "Completed"' in js
    assert 'el("span", { class: "key sr-only" }, `Scenario ${sc.key} · `)' in js
    assert 'o.text !== o.brief ? el("span", { class: "sr-only" }, ` (${o.text})`)' in js
    # The backend's current settings are shown with the audit, not in the Demo view.
    assert 'body[data-view="demo"] .system .sys-label' in css and 'document.body.dataset.view = dev ? "developer" : "demo"' in js


def test_provenance_chips_show_the_state_the_evidence_supports():
    _, js, _ = _static()
    stages = js[js.index("function interpretationStage(b)") : js.index("function provenanceNote(")]
    # Meta live, Meta recorded, mock, not needed or off: each from its own record, in short words.
    assert 'chip: "Interpretation · Meta recorded"' in stages and 'chip: "Interpretation · None"' in stages
    assert 'const when = mode === "live" ? "live" : "recorded";' in stages
    assert "chip: `Recovery · Meta real · ${when}`" in stages and 'kind: mode === "live" ? "real" : "recorded-real"' in stages
    assert 'chip: mock ? "Recovery · Mock"' in stages and 'none("Recovery · Not needed"' in stages
    assert 'sc.healing ? "Recovery · Not attempted" : "Recovery · Off"' in stages
    assert "Recovery · ${advisor} · in progress" in stages and "· playback" in stages
    # The Recovery panel's source: Meta, Mock or None; counts only when the evidence stores them.
    assert "source: `Meta Muse Spark · ${when}${n > 0 ? ` · ${plural(n, \"request\")}` : \"\"}`" in stages
    assert 'source: mock ? `Mock · simulated${b.ai.provider === "meta" ? "" : " · 0 Meta requests"}`' in stages
    assert 'source: "None"' in stages
    # A simulated answer is never labelled as Meta or as real.
    mock_chip = stages[stages.index('chip: mock ? "Recovery · Mock"') :].split("\n")[0]
    assert "Meta" not in mock_chip and "real" not in mock_chip
    # The row describes the selected scenario only: never a sum of different executions.
    row = js[js.index("function renderProvRow()") : js.index("// Developer view (and the expanded dialog)")]
    assert "[interpretationStage(b), recoveryStage(b, sc)]" in row


def _rules(css: str):
    """(selector, declarations) for every plain rule, including those inside @media blocks."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}@;]+)\{([^{}]*)\}", css)]


def test_palette_is_neutral_blue_led_with_minimal_violet_and_aa_contrast():
    from test_demo_console import _contrast, _css_vars

    html, _, css = _static()
    v = _css_vars(css)
    # The brief's palette: neutral surfaces, blue accent, violet only as a small detail.
    assert (v["bg"], v["surface"], v["line"], v["blue"], v["blue-soft"], v["text"], v["violet"]) == (
        "#f5f7fa", "#ffffff", "#e2e8f0", "#1877f2", "#eaf2ff", "#1e293b", "#8b5cf6")  # fmt: skip
    # Secondary text is a touch darker than slate-500 so it passes AA on every surface it sits on.
    for fg, bg in [("muted", "bg"), ("muted", "surface"), ("muted", "surface-2"), ("muted", "blue-soft"), ("faint", "bg"),
                   ("blue-ink", "blue-soft"), ("ok-ink", "ok-soft"), ("ok-ink", "surface"), ("fail-ink", "fail-soft"), ("text", "surface-2")]:  # fmt: skip
        assert _contrast(v[fg], v[bg]) >= 4.5, (fg, bg, round(_contrast(v[fg], v[bg]), 2))
    assert _contrast("#ffffff", v["blue-ink"]) >= 4.5  # primary buttons and the selected tab
    # No blue-to-violet gradients; in the Demo view violet only marks a recovery (or the proud ghost).
    assert not re.search(r"gradient\([^;]*--violet", css)
    dev_start, dev_end = css.index("/* developer view"), css.index("/* AI provenance, one block per stage")
    demo_css = css[:dev_start] + css[dev_end:]
    violet = [sel for sel, body in _rules(demo_css) if "--violet" in body and not sel.startswith(":root")]
    assert violet and all("recovered" in sel or "proud" in sel for sel in violet), violet
    # The mascot keeps its body, ring and sparkles; only its aura is softer and blue.
    svg = html[html.index('<svg class="mascot"') : html.index("</svg>", html.index('<svg class="mascot"'))]
    assert '<stop offset="1" stop-color="#d8d0ff"/>' in svg and '<stop offset="1" stop-color="#8f6bff"/>' in svg
    glow = svg[svg.index('id="gf-glow"') : svg.index("</radialGradient>", svg.index('id="gf-glow"'))]
    assert "8f6bff" not in glow and ".mascot { width: 88px; height: 88px;" in css
