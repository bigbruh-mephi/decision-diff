"""Self-contained downloadable HTML report: verdict, decision swap, change clusters and every check row.

No scripts and no external resources (strict CSP); every number links back into the application, so the analyst
can drill from the downloaded file into the interactive report.
"""

import html
import urllib.parse

import orjson

from .errors import explain

CSS = """
:root{--bg:#fff;--fg:#0f0f0f;--muted:#6b6560;--line:#eee9e3;--fail:#ad1242;--fail-bg:#fdebf0;--low:#8c5a68;
--low-bg:#faf3f5;--pass:#1f7a4d;--accent:#d93a14}
@media (prefers-color-scheme:dark){:root{--bg:#151210;--fg:#f5f1ed;--muted:#a89f97;--line:#2f2925;--fail:#ff7a9c;
--fail-bg:#3b1722;--low:#c59aa6;--low-bg:#2a1f22;--pass:#6fcf9f;--accent:#ff8562}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 Inter,system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;
margin:32px 0 10px}p.sub{color:var(--muted);margin:0 0 20px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,
minmax(170px,1fr));gap:10px}.tile{border:1px solid var(--line);border-radius:20px;padding:12px 14px}.tile b{display:
block;font-size:24px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:12px}table{width:100%;
border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
vertical-align:top}th{color:var(--muted);font-weight:600;font-size:12px}td.n{text-align:right;font-variant-numeric:
tabular-nums;white-space:nowrap}code{font:12px ui-monospace,JetBrains Mono,monospace}a{color:var(--accent)}
tr.fail td{background:var(--fail-bg)}tr.fail .res{color:var(--fail);font-weight:700}tr.low td{background:var(--low-bg);
color:var(--low)}tr.low .res{color:var(--low);font-weight:600}tr.pass .res{color:var(--pass);font-weight:600}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid var(--line);font-size:11px}
.wrap{overflow-x:auto}footer{margin-top:40px;color:var(--muted);font-size:12px}
"""



def _e(x):
    return html.escape("" if x is None else str(x))


def _pct(n, d):
    return f"{n / d * 100:.1f}%".replace(".", ",") if d else "—"


def _n(x):
    return f"{x:,}".replace(",", "\u00a0") if isinstance(x, int) else _e(x)


ORIGIN = {"input": "входные данные", "logic": "логика стратегии", "mixed": "входные данные и логика"}


def render(rep, run, check_results, base):
    """HTML text of the report for ``rep`` (a RunReport) and the run row; links point to ``base``."""
    rid = str(run["id"])
    app_url = f"{base}/runs/{rid}"
    s = rep.summary({})
    clusters = rep.cluster_rows({})

    def link(text, view="", scope=None, tab=None):
        q = "?scope=" + urllib.parse.quote(orjson.dumps(scope).decode()) if scope else f"?tab={tab}" if tab else ""
        return f'<a href="{_e(app_url + view + q)}">{_e(text)}</a>'

    lb = s["labels"]
    obj = (lb.get("object") or ["", "", "объектов"])[2]
    tiles = [(obj.capitalize(), s["apps"], ""), ("С различиями", s["changed"], _pct(s["changed"], s["apps"])),
             ("Сменилось решение", s["decision_changed"], _pct(s["decision_changed"], s["apps"])),
             ("Различия есть, решение то же", s["changed_same_decision"], _pct(s["changed_same_decision"], s["apps"]))]
    if s["approval_before"] is not None:
        tiles.append((f"{lb.get('positive_rate', 'доля положительного исхода').capitalize()} (BEFORE → AFTER)", f"{_pct(s['approval_before'], 1)} → {_pct(s['approval_after'], 1)}", ""))
    fin = run.get("finished_at")
    fin = fin.strftime("%d.%m.%Y %H:%M %Z") if hasattr(fin, "strftime") else fin
    out = [(f"<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport "
           f"content='width=device-width,initial-scale=1'><title>Отчёт {_e(run['name'])}</title><style>{CSS}</style>"
           f"</head><body><main><h1>Прогон {_e(run['name'])}</h1><p class=sub>Анализ изменений BEFORE (MASTER) и AFTER"
           f" (BETA) · завершён {_e(fin)} · {link('интерактивный отчёт')} · {link('сводная таблица', tab='pivot')}"
           f" · {link('автопроверки', tab='checks')}</p><div class=tiles>")]
    out += [f"<div class=tile><b>{_n(v)}</b><span>{_e(k)} {_e(p)}</span></div>" for k, v, p in tiles]
    out.append(f"</div><h2>Куда сместились решения</h2><div class=wrap><table><tr><th>BEFORE<th>AFTER<th>{_e(obj.capitalize())}")
    out += [f"<tr><td>{_e(r['before'])}<td>{_e(r['after'])}<td class=n>{_n(r['apps'])}" for r in s["swap"]]
    out.append("</table></div><h2>Кластеры изменений</h2><div class=wrap><table><tr><th>№<th>Источник<th>"
               f"{_e(obj.capitalize())}<th>Сменилось решение<th>Причина смены решения")
    for c in clusters:
        if c.get("cid") is None:
            out.append(f"<tr><td>…<td>ещё кластеров: {_n(c['clusters'])}<td class=n>{_n(c['apps'])}"
                       f"<td class=n>{_n(c['decision_changed'])}<td>")
            continue
        tr = "<br>".join(f"{_e(x['label'])} ({_n(x['apps'])})" for x in c["causes"])
        out.append(f"<tr><td>{link(c['cid'], f'/clusters/{c['cid']}')}<td><span class=badge>"
                   f"{_e(ORIGIN.get(c['origin'], c['origin']))}</span><td class=n>{_n(c['apps'])}"
                   f"<td class=n>{_n(c['decision_changed'])}<td>{tr}")
    out.append(f"</table></div><h2>Автопроверки · {link('открыть в приложении', tab='checks')}</h2><div class=wrap>"
               "<table><tr><th>Проверка<th>Срез<th>Аргумент<th>Числа<th>Метрика<th>Результат<th>Заявки")
    rows = [(r["label"], x, r.get("check_sha"), k) for r in check_results for k, x in enumerate(r["rows"])]
    rows.sort(key=lambda t: (t[1]["RESULT"] != "FAIL", t[1]["CONFIDENCE"] != "HIGH", t[0]))
    for label, x, sha, k in rows:
        cls = "pass" if x["RESULT"] == "PASS" else ("low" if x["CONFIDENCE"] == "LOW" else "fail")
        res = x["RESULT"] + (" · низкая уверенность" if x["CONFIDENCE"] == "LOW" else "")
        scope = {"check": {"sha": sha, "row": k, "cnt": x["CNT"]}} if x["CNT"] > len(x["APP_IDS"]) else \
            {"apps": x["APP_IDS"]}
        apps = link(f"открыть {_n(x['CNT'])}", "/apps", scope) if x["CNT"] and (x["APP_IDS"] or sha) else "—"
        out.append(f"<tr class={cls}><td>{_e(label)}<td>{_e(x['SOURCE'])}<td><code>{_e(x['ARGUMENT'])}</code>"
                   f"<td class=n>{_n(x['CNT'])} / {_n(x['TOTAL'])}<td class=n>{_e(x['METRIC'])}<td class=res>{_e(res)}"
                   f"<td class=n>{apps}")
    for r in check_results:
        if r.get("error"):
            out.append(f"<tr class=fail><td>{_e(r['label'])}<td colspan=6>не выполнилась: {_e(explain(r['error']))}")
    out.append(f"</table></div><footer>Decision Diff · прогон {_e(rid)} · файл автономный, ссылки открывают "
               f"{_e(base)}</footer></main></body></html>")
    return "".join(out)
